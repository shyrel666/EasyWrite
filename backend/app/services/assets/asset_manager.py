import copy
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from app.core.config import settings
from app.models.schemas import (
    AssetAttachment, CompanyQualification, PersonnelAsset, CaseContract, SolutionComponent, MaterialCheck,
)
from app.services.assets.matching import best_match
from app.services.assets.material_check import MATERIAL_KINDS, check_material

logger = logging.getLogger("easywrite.assets")

ASSET_KINDS = ("qualifications", "personnel", "cases", "components")
ASSET_STATUSES = ("example", "unverified", "confirmed")
ASSET_MODELS = {
    "qualifications": CompanyQualification,
    "personnel": PersonnelAsset,
    "cases": CaseContract,
    "components": SolutionComponent,
}

# 证明附件：只接受 PDF 与常见图片（按文件头识别，不信任扩展名），单个不超过 20MB
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
ATTACHMENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


class AttachmentError(ValueError):
    """附件不合法（类型、大小、所属资料），消息可直接展示给用户"""


def sniff_attachment_type(data: bytes) -> Optional[str]:
    """按文件头识别附件类型，返回 MIME；不是 PDF/图片时返回 None"""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class EnterpriseAssetManager:
    """
    企业统一中台资产管理服务（借鉴 Yibiao-Web 企业集中资产中台）：
    1. 集中沉淀企业四大核心资产库：
       - ① 公司资质认证库 (Company Qualifications)
       - ② 技术骨干与人员证书履历库 (Personnel & Certifications)
       - ③ 历史同类中标业绩案例库 (Historical Cases & Contracts)
       - ④ 标准方案可复用组件库 (Reusable Solution Components)
    2. 支持标签、分类检索与全文匹配
    3. 支持在标书智能草拟时（如项目团队、资质要求、业绩案例、技术模块）自动命中与注入
    4. 本地持久化至 data/enterprise_assets.json
    """

    DEFAULT_QUALIFICATIONS = [
        {
            "id": "qual_cmmi5",
            "name": "CMMI 5级 软件成熟度能力最高级别认证",
            "cert_no": "CMMI-V2.0-5-2023-0988",
            "category": "研发能力",
            "level": "5级",
            "issue_org": "CMMI Institute",
            "issue_date": "2023-11-15",
            "expiry_date": "2026-11-14",
            "summary": "全球公认的软件工程开发与过程管理最高级别认证，在技术标评分中通常占满分4~5分",
            "proof_doc": "附录A-01_CMMI5认证证书扫描件及官网公示截屏.pdf"
        },
        {
            "id": "qual_cs4",
            "name": "国家信息系统建设和服务能力评估 CS4 级 (优秀级)",
            "cert_no": "CS-2024-4-00129",
            "category": "综合资质",
            "level": "CS4级",
            "issue_org": "中国电子信息行业联合会",
            "issue_date": "2024-03-20",
            "expiry_date": "2028-03-19",
            "summary": "国内信息化与系统集成领域权威能力认证，证明具备承担国家级重大数字化工程总集成能力",
            "proof_doc": "附录A-02_CS4证书及能力评估报告.pdf"
        },
        {
            "id": "qual_secret",
            "name": "涉密信息系统集成资质（系统集成 / 软件开发 双甲级）",
            "cert_no": "JC-2022-A-0056",
            "category": "安全保密",
            "level": "甲级",
            "issue_org": "国家保密局 / 国家保密资质认证中心",
            "issue_date": "2022-08-10",
            "expiry_date": "2027-08-09",
            "summary": "承接国家级政务内网、涉密专网及高安全涉密数字化项目的强制法定准入资质",
            "proof_doc": "附录A-03_涉密信息系统集成双甲级证书.pdf"
        },
        {
            "id": "qual_iso_triple",
            "name": "ISO三体系认证（ISO9001质量 / ISO27001信息安全 / ISO20000IT服务管理）",
            "cert_no": "ISO-ALL-2023-8812",
            "category": "质量体系",
            "level": "标准级",
            "issue_org": "中国质量认证中心 (CQC) / UKAS",
            "issue_date": "2023-05-12",
            "expiry_date": "2026-05-11",
            "summary": "标准化三体系认证，覆盖软件研发、运维管理、网络安全合规全生命周期",
            "proof_doc": "附录A-04_ISO三体系认证证书合集.pdf"
        },
        {
            "id": "qual_itss1",
            "name": "ITSS 信息技术服务标准运行维护服务能力成熟度一级 (最高级)",
            "cert_no": "ITSS-2023-YW-108",
            "category": "服务运维",
            "level": "一级",
            "issue_org": "中国电子工业标准化技术协会",
            "issue_date": "2023-09-01",
            "expiry_date": "2027-08-31",
            "summary": "国内 IT 运维最高级别证书，在运维保障体系评分项中直接获得满分",
            "proof_doc": "附录A-05_ITSS一级证书.pdf"
        }
    ]

    DEFAULT_PERSONNEL = [
        {
            "id": "person_01",
            "name": "张文远",
            "role": "项目经理 / 项目总监",
            "years_of_experience": 16,
            "education": "北京航空航天大学 计算机软件与理论 硕士",
            "professional_title": "教授级高级工程师、信息系统项目管理师 (软考高项)",
            "certificates": ["PMP (项目管理专业人士)", "信息系统项目管理师 (高级)", "ITIL Expert", "Scrum Master (CSM)"],
            "representative_projects": [
                "某直辖市数字政府一体化调度总指挥平台项目 (合同额 2,800 万元)",
                "国家某部委核心业务协同监管云工程 (合同额 1,650 万元)"
            ],
            "intro": "拥有16年政企特大型信息化工程管理经验，主持过4项国家级及省级重点数字化系统建设，擅长敏捷研发、风险管控与多方团队协同，多次荣获省部级优秀项目经理表彰。"
        },
        {
            "id": "person_02",
            "name": "陈若曦",
            "role": "技术总监 / 首席系统架构师",
            "years_of_experience": 14,
            "education": "清华大学 计算机科学与技术 硕士",
            "professional_title": "系统分析师 (高级)、系统架构设计师 (高级)",
            "certificates": ["系统架构设计师 (高级)", "TOGAF 9.2 鉴定企业架构师", "AWS Certified Solutions Architect", "达梦数据库认证专家 (DMCA)"],
            "representative_projects": [
                "某省级智慧水务与水旱灾害综合调度平台 (分布式微服务架构，支撑日均亿级数据)",
                "某国有商业银行信创业务中台重构工程"
            ],
            "intro": "资深云原生与分布式微服务架构技术权威，深谙信创生态适配与高可用容灾设计，具有达梦/人大金仓及麒麟/统信OS全栈兼容实战经验，主导编写过数十万行核心基础中台代码。"
        },
        {
            "id": "person_03",
            "name": "刘德铭",
            "role": "信息安全与网络安全总监",
            "years_of_experience": 12,
            "education": "中国科学技术大学 信息安全 本科",
            "professional_title": "高级工程师",
            "certificates": ["CISP (注册信息安全专业人员)", "CISSP (国际注册信息系统安全认证)", "CISP-PTE (渗透测试专家)", "国密密码应用测评师"],
            "representative_projects": [
                "某省政务专网等保三级测评与密评升级项目",
                "市级智慧政务云平台安全态势感知与纵深防御系统"
            ],
            "intro": "12年网络空间安全与数据合规经验，精通国家网络安全等级保护（三级）标准及国密 SM2/SM3/SM4 算法改造，多次带领团队零失误通过国家重大活动护网与专项安全攻防演习。"
        },
        {
            "id": "person_04",
            "name": "赵晓宇",
            "role": "售后运维与交付保障总监",
            "years_of_experience": 10,
            "education": "华中科技大学 软件工程 本科",
            "professional_title": "高级工程师",
            "certificates": ["ITSS 服务项目经理", "CCIE (思科认证互联网专家)", "RHCA (红帽认证架构师)"],
            "representative_projects": [
                "某市级数字化城市运行管理中心 5 年长效驻场运维保障",
                "全省教育综合服务平台 7×24 小时应急保障工程"
            ],
            "intro": "具备完备的 ITIL / ITSS 运维服务体系沉淀，擅长搭建 7×24 小时监控预警联动平台，实现 5 分钟响应、30 分钟远程排错、2 小时驻场极速处置的高标准 SLA 承诺。"
        }
    ]

    DEFAULT_CASES = [
        {
            "id": "case_01",
            "project_name": "某省智慧政务一体化综合大数据调度中台建设项目",
            "client_name": "某省政务服务和数字化建设管理局",
            "contract_amount": "2,480.00 万元",
            "sign_date": "2024-03",
            "contract_category": "政务大数据",
            "key_deliverables": [
                "全省统一政务数据资产目录系统",
                "秒级跨部门数据共享交换总线 (ESB)",
                "亿级实时数据湖与信创达梦数据库集群",
                "可视化领导决策大屏驾驶舱"
            ],
            "acceptance_status": "已于 2024 年 12 月以“优秀”等次通过省厅专家联合终验，连续平稳运行超 200 天",
            "summary": "该项目为国内省级政务中台标杆工程，承接全省 42 个委办局业务数据汇聚，入选省级数字化转型十大示范工程，业主出具了高度满意的官方表扬信。"
        },
        {
            "id": "case_02",
            "project_name": "市级智慧水务一体化综合调度与管网数字孪生工程",
            "client_name": "某市水务环境集团有限公司",
            "contract_amount": "1,520.00 万元",
            "sign_date": "2023-08",
            "contract_category": "智慧水务",
            "key_deliverables": [
                "全域水质、水压、水文实时 IoT 采集平台",
                "管网水力模型与漏损 AI 预警引擎",
                "防汛排涝防灾应急多跨协同调度指挥中心",
                "国产信创双活数据容灾中心"
            ],
            "acceptance_status": "已终验交付，管网漏损率降低 3.2%，综合能耗降低 8.5%",
            "summary": "构建了全市统一水务“一网统管”新格局，成功应对多次极端暴雨汛情考验，多次接待住建部与水利部专家考察调研。"
        },
        {
            "id": "case_03",
            "project_name": "某国有大型商业银行信创协同办公与业务安全网关系统",
            "client_name": "某国有商业银行总行软件开发中心",
            "contract_amount": "1,850.00 万元",
            "sign_date": "2023-11",
            "contract_category": "金融信创",
            "key_deliverables": [
                "全栈信创架构微服务网关 (Spring Cloud + Istio)",
                "国产密码算法 (SM2/SM3/SM4) 全链路加密",
                "达梦数据库双活容灾与异地灾备集群",
                "高并发金融级 API 鉴权中心 (万级 TPS)"
            ],
            "acceptance_status": "已终验并上线投产，保障全行超 8 万名员工及分支机构高并发访问",
            "summary": "国内金融行业信创替换重点标杆案例，通过中国信息通信研究院信创全面兼容性认证，实现零缺陷、零故障平滑过渡。"
        }
    ]

    DEFAULT_COMPONENTS = [
        {
            "id": "comp_01",
            "name": "高可用双活与容灾架构方案组件",
            "category": "容灾高可用",
            "tags": ["高可用", "双活", "RPO=0", "容灾备份", "达梦"],
            "summary": "针对政企核心业务数据零丢失、业务不中断的高标准容灾技术体系设计方案",
            "content": (
                "### 1. 容灾架构目标与设计原则\n"
                "本方案严格遵循国家信息安全等级保护三级数据备份要求，设计“同城双活 + 异地灾备”的三中心容灾拓扑：\n"
                "- **RPO（恢复点目标）= 0**：核心数据库采用强半同步机制与物理日志实时同步，确保在单数据中心突发断电或灾难时零数据丢失；\n"
                "- **RTO（恢复时间目标）< 30 秒**：采用集群智能心跳探测与虚拟 IP 漂移技术，实现秒级故障自动隔离与应用流量无缝倒换；\n"
                "- **多副本与离线冷备**：关键业务数据每日实行全量冷备并进行国密加密后分片存储至隔离存储卷，定期执行自动化数据可恢复性演练。"
            )
        },
        {
            "id": "comp_02",
            "name": "国家网络安全等级保护（三级）与国密合规纵深防御组件",
            "category": "安全等保",
            "tags": ["等保三级", "国密SM4", "安全合规", "纵深防御", "审计"],
            "summary": "覆盖物理安全、网络边界、主机环境、应用系统与数据安全的等保三级全栈合规论述",
            "content": (
                "### 1. 纵深防御体系与等保三级对标\n"
                "针对项目安全需求，系统严格按照 GB/T 22239-2019《信息安全技术 网络安全等级保护基本要求》第三级标准设计：\n"
                "- **通信安全与传输加密**：全面采用国产商用密码算法（SM2 非对称公钥加密、SM3 完整性杂凑校验、SM4 对称分组加密），全站实行国密 SSL/TLS 双向鉴权通信；\n"
                "- **访问控制与最小权限**：采用基于 RBAC 的精细化权限模型，实现用户、角色、资源的严格隔离；关键管理操作支持双人复核审批；\n"
                "- **全生命周期安全审计**：独立部署不可篡改的日志审计子系统，对所有数据修改、系统登录、配置变更实行秒级日志留痕，日志安全留存期不少于 180 天。"
            )
        },
        {
            "id": "comp_03",
            "name": "国产信创生态全栈适配与兼容互认方案组件",
            "category": "信创适配",
            "tags": ["信创", "国产化", "达梦", "统信UOS", "银河麒麟", "鲲鹏/飞腾"],
            "summary": "涵盖国产CPU架构、操作系统、中间件与数据库的原厂兼容与调优方案",
            "content": (
                "### 1. 信创全生态适配蓝图\n"
                "我司自研核心平台已实现与国内主流信创基础软硬件的深度适配与双向兼容互认证：\n"
                "- **芯片与服务器底座**：原生支持 ARM64 (鲲鹏、飞腾) 及 LoongArch (龙芯)、x86 (海光、兆芯) 等全技术路线服务器架构；\n"
                "- **操作系统层**：完全适配银河麒麟 (Kylin Linux Advanced Server V10) 与统信桌面及服务器系统 (UnionTech OS Server 20)；\n"
                "- **国产信创数据库**：已获得达梦数据库 (DM8)、人大金仓 (KingbaseES V8) 的原厂技术互认证书，具备专用连接池优化器与高并发方言适配包。"
            )
        },
        {
            "id": "comp_04",
            "name": "7×24 小时高可用服务运维保障体系与 SLA 承诺组件",
            "category": "运维交付",
            "tags": ["SLA", "7x24", "运维保障", "应急预案", "巡检"],
            "summary": "标准化 ITSS 运维服务流程、分级故障应急响应与专属驻场服务方案",
            "content": (
                "### 1. 售后运维组织架构与 SLA 服务承诺\n"
                "我方针对本项目设立专属“两级技术支持与现场常驻专家组”，提供如下顶级 SLA 服务保障：\n"
                "- **响应时限指标**：设立 7×24 小时全国统一服务专线与专属企微/钉钉应急群，故障发生后 **5 分钟内**响应接单并由高级工程师接入诊断；\n"
                "- **远程处置与驻场到达**：一般咨询与配置故障在 **30 分钟内**远程闭环处置；如遇重大级别故障，本地专职工程师在 **2 小时内**赶赴现场支持；\n"
                "- **主动预防性健康巡检**：提供每月一次远程系统深度巡检、每季度一次原厂驻场健康体检，出具专业性能诊断与系统容量调优报告；重大节假日与保障期实行全天候专家驻场护航。"
            )
        }
    ]

    def __init__(self, storage_file: Optional[Path] = None):
        self.storage_file = storage_file or settings.DATA_DIR / "enterprise_assets.json"
        self.files_dir = self.storage_file.parent / "asset_files"
        self._lock = threading.RLock()
        self.qualifications: List[Dict[str, Any]] = []
        self.personnel: List[Dict[str, Any]] = []
        self.cases: List[Dict[str, Any]] = []
        self.components: List[Dict[str, Any]] = []
        self._load_or_init()

    def _defaults(self, kind: str) -> List[Dict[str, Any]]:
        """预设示例（深拷贝，标记为 example：只展示录入格式，不进入撰写提示词）"""
        return [dict(item, status="example") for item in copy.deepcopy(getattr(self, f"DEFAULT_{kind.upper()}"))]

    def _load_or_init(self):
        """
        读取资料文件。只有文件不存在（首次启动）时才写入预设示例；
        文件存在时某一类为空就保持为空——用户删空的资料不能在重启后被示例补回或连带重置其他类。
        文件损坏时先把原文件备份为 *.corrupt-时间.bak，再以空库启动，不用示例覆盖。
        旧数据没有 status：ID 属于预设示例的标为 example，其余视为用户录入、标为 confirmed，并写回文件。
        """
        with self._lock:
            if not self.storage_file.exists():
                for kind in ASSET_KINDS:
                    setattr(self, kind, self._defaults(kind))
                self._save()
                return

            try:
                data = json.loads(self.storage_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("资料文件顶层不是 JSON 对象")
            except Exception:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = self.storage_file.with_name(f"{self.storage_file.name}.corrupt-{stamp}.bak")
                logger.exception("企业资料文件无法解析，已备份为 %s，资料库以空库启动", backup.name)
                os.replace(self.storage_file, backup)
                data = {}

            migrated = False
            for kind in ASSET_KINDS:
                items = data.get(kind)
                items = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
                example_ids = {x["id"] for x in getattr(self, f"DEFAULT_{kind.upper()}")}
                for item in items:
                    if item.get("status") not in ASSET_STATUSES:
                        item["status"] = "example" if item.get("id") in example_ids else "confirmed"
                        migrated = True
                setattr(self, kind, items)
            if migrated or not data:
                self._save()  # 写回迁移后的状态；损坏文件已备份时写回空库，避免下次启动补入示例

    def _save(self):
        """先写临时文件再原子替换，写到一半中断也不会留下截断的资料文件；失败时抛出异常"""
        payload = {kind: getattr(self, kind) for kind in ASSET_KINDS}
        tmp = self.storage_file.with_name(f"{self.storage_file.name}.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.storage_file)
        except Exception:
            logger.exception("保存企业资料文件失败")
            tmp.unlink(missing_ok=True)
            raise

    def _commit(self, updates: Dict[str, List[Dict[str, Any]]]):
        """替换一类或多类资料并落盘；写盘失败时恢复内存中的原列表，保证内存与文件一致"""
        with self._lock:
            previous = {kind: getattr(self, kind) for kind in updates}
            for kind, items in updates.items():
                setattr(self, kind, items)
            try:
                self._save()
            except Exception:
                for kind, items in previous.items():
                    setattr(self, kind, items)
                raise

    def _add(self, kind: str, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        新增或更新：ID 已存在时原位替换（编辑资料），否则追加；返回实际保存的条目。
        证明附件只经附件接口增删：编辑时保留服务端已有的附件，新建时忽略客户端提交的附件。
        确认时间由服务端记录：改为"已确认"时记下当前时间，已确认的条目保留原时间，其他状态清空。
        """
        with self._lock:
            current = getattr(self, kind)
            previous = next((x for x in current if x.get("id") == item["id"]), None)
            if kind in MATERIAL_KINDS:
                item["attachments"] = copy.deepcopy((previous or {}).get("attachments", []))
                was_confirmed = previous is not None and previous.get("status") == "confirmed"
                if item.get("status") != "confirmed":
                    item["confirmed_at"] = ""
                elif was_confirmed and previous.get("confirmed_at"):
                    item["confirmed_at"] = previous["confirmed_at"]
                else:
                    item["confirmed_at"] = _now()
            if previous is not None:
                items = [item if x.get("id") == item["id"] else x for x in current]
            else:
                items = current + [item]
            self._commit({kind: items})
            return item

    def _delete(self, kind: str, item_id: str) -> bool:
        with self._lock:
            current = getattr(self, kind)
            remaining = [x for x in current if x.get("id") != item_id]
            if len(remaining) == len(current):
                return False
            self._commit({kind: remaining})
        self._remove_files(item_id)
        return True

    # ==================== 证明附件 ====================

    def _asset_dir(self, asset_id: str) -> Path:
        """资料的附件目录；ID 含路径字符时改用其摘要作目录名，保证不越出 asset_files"""
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", asset_id):
            return self.files_dir / asset_id
        return self.files_dir / ("id_" + hashlib.sha1(asset_id.encode("utf-8")).hexdigest()[:16])

    def _remove_files(self, asset_id: str):
        folder = self._asset_dir(asset_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    def get_asset(self, kind: str, asset_id: str) -> Optional[Dict[str, Any]]:
        """按类别与 ID 取资料（字典副本）；不存在时返回 None"""
        if kind not in ASSET_KINDS:
            return None
        return next((copy.deepcopy(x) for x in getattr(self, kind) if x.get("id") == asset_id), None)

    def _require_material(self, kind: str, asset_id: str) -> Dict[str, Any]:
        if kind not in MATERIAL_KINDS:
            raise AttachmentError("只有资质、人员、业绩资料可以上传证明附件")
        item = self.get_asset(kind, asset_id)
        if item is None:
            raise KeyError(asset_id)
        return item

    def list_attachments(self, kind: str, asset_id: str) -> List[AssetAttachment]:
        item = self._require_material(kind, asset_id)
        return [AssetAttachment(**a) for a in item.get("attachments", [])]

    def add_attachment(self, kind: str, asset_id: str, filename: str, data: bytes) -> AssetAttachment:
        """保存证明附件：先写文件再登记到资料；登记失败（含上传期间资料被删除）时删除已写入的文件"""
        self._require_material(kind, asset_id)
        ext = Path(filename or "").suffix.lower()
        if ext not in ATTACHMENT_TYPES:
            raise AttachmentError("证明附件只支持 PDF 与图片（png / jpg / gif / bmp / webp / tif）")
        if not data:
            raise AttachmentError("附件为空文件")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise AttachmentError("单个附件不能超过 20MB")
        sniffed = sniff_attachment_type(data)
        if sniffed is None or sniffed != ATTACHMENT_TYPES[ext]:
            raise AttachmentError("文件内容与扩展名不符，或不是 PDF / 图片文件")

        meta = AssetAttachment(
            id=f"att_{uuid.uuid4().hex[:12]}",
            filename=Path(filename).name[:200],
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=sniffed,
            uploaded_at=_now(),
        )
        folder = self._asset_dir(asset_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{meta.id}{ext}"
        path.write_bytes(data)
        try:
            with self._lock:
                current = getattr(self, kind)
                if not any(x.get("id") == asset_id for x in current):
                    raise KeyError(asset_id)
                items = [
                    dict(x, attachments=list(x.get("attachments", [])) + [meta.model_dump()])
                    if x.get("id") == asset_id else x
                    for x in current
                ]
                self._commit({kind: items})
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return meta

    def attachment_file(self, kind: str, asset_id: str, file_id: str) -> Tuple[Path, AssetAttachment]:
        """返回附件文件路径与登记信息；不存在时抛出 KeyError"""
        for att in self.list_attachments(kind, asset_id):
            if att.id == file_id:
                path = self._asset_dir(asset_id) / f"{att.id}{Path(att.filename).suffix.lower()}"
                if not path.exists():
                    raise KeyError(file_id)
                return path, att
        raise KeyError(file_id)

    def delete_attachment(self, kind: str, asset_id: str, file_id: str) -> bool:
        """先注销登记再删文件：登记写盘失败时文件仍在，资料与文件保持一致"""
        self._require_material(kind, asset_id)
        with self._lock:
            current = getattr(self, kind)
            target = next((x for x in current if x.get("id") == asset_id), {})
            atts = target.get("attachments", [])
            removed = next((a for a in atts if a.get("id") == file_id), None)
            if removed is None:
                return False
            items = [
                dict(x, attachments=[a for a in atts if a.get("id") != file_id]) if x.get("id") == asset_id else x
                for x in current
            ]
            self._commit({kind: items})
        (self._asset_dir(asset_id) / f"{file_id}{Path(removed.get('filename', '')).suffix.lower()}").unlink(missing_ok=True)
        return True

    def clear_examples(self) -> Dict[str, int]:
        """一次性删除四类资料中的全部预设示例，返回各类删除条数"""
        with self._lock:
            updates, removed, removed_ids = {}, {}, []
            for kind in ASSET_KINDS:
                current = getattr(self, kind)
                kept = [x for x in current if x.get("status") != "example"]
                removed[kind] = len(current) - len(kept)
                removed_ids += [x.get("id", "") for x in current if x.get("status") == "example"]
                if removed[kind]:
                    updates[kind] = kept
            if updates:
                self._commit(updates)
        for item_id in removed_ids:
            self._remove_files(item_id)
        return removed

    # ==================== 1. 资质库 CRUD ====================

    def list_qualifications(self, category: Optional[str] = None, search: Optional[str] = None) -> List[CompanyQualification]:
        results = [CompanyQualification(**q) for q in self.qualifications]
        if category:
            results = [q for q in results if q.category == category]
        if search:
            s = search.lower()
            results = [q for q in results if s in q.name.lower() or s in q.cert_no.lower() or s in q.summary.lower()]
        return results

    def add_qualification(self, qual: CompanyQualification) -> CompanyQualification:
        if not qual.id:
            qual.id = f"qual_{uuid.uuid4().hex[:8]}"
        return CompanyQualification(**self._add("qualifications", qual.model_dump()))

    def delete_qualification(self, qual_id: str) -> bool:
        return self._delete("qualifications", qual_id)

    # ==================== 2. 人员证书库 CRUD ====================

    def list_personnel(self, role: Optional[str] = None, search: Optional[str] = None) -> List[PersonnelAsset]:
        results = [PersonnelAsset(**p) for p in self.personnel]
        if role:
            results = [p for p in results if role in p.role]
        if search:
            s = search.lower()
            results = [p for p in results if s in p.name.lower() or s in p.role.lower() or any(s in c.lower() for c in p.certificates)]
        return results

    def add_personnel(self, person: PersonnelAsset) -> PersonnelAsset:
        if not person.id:
            person.id = f"person_{uuid.uuid4().hex[:8]}"
        return PersonnelAsset(**self._add("personnel", person.model_dump()))

    def delete_personnel(self, person_id: str) -> bool:
        return self._delete("personnel", person_id)

    # ==================== 3. 历史同类业绩库 CRUD ====================

    def list_cases(self, category: Optional[str] = None, search: Optional[str] = None) -> List[CaseContract]:
        results = [CaseContract(**c) for c in self.cases]
        if category:
            results = [c for c in results if c.contract_category == category]
        if search:
            s = search.lower()
            results = [c for c in results if s in c.project_name.lower() or s in c.client_name.lower() or s in c.summary.lower()]
        return results

    def add_case(self, case_item: CaseContract) -> CaseContract:
        if not case_item.id:
            case_item.id = f"case_{uuid.uuid4().hex[:8]}"
        return CaseContract(**self._add("cases", case_item.model_dump()))

    def delete_case(self, case_id: str) -> bool:
        return self._delete("cases", case_id)

    # ==================== 4. 方案组件库 CRUD ====================

    def list_components(self, category: Optional[str] = None, search: Optional[str] = None) -> List[SolutionComponent]:
        results = [SolutionComponent(**comp) for comp in self.components]
        if category:
            results = [comp for comp in results if comp.category == category]
        if search:
            s = search.lower()
            results = [comp for comp in results if s in comp.name.lower() or s in comp.summary.lower() or any(s in t.lower() for t in comp.tags)]
        return results

    def add_component(self, comp: SolutionComponent) -> SolutionComponent:
        if not comp.id:
            comp.id = f"comp_{uuid.uuid4().hex[:8]}"
        return SolutionComponent(**self._add("components", comp.model_dump()))

    def delete_component(self, comp_id: str) -> bool:
        return self._delete("components", comp_id)

    # ==================== 5. 撰写时的资料匹配 ====================

    # 每章最多注入的资料条数：只取与本章相关的条目，不整库注入
    MAX_MATCHED = 8
    # 相关度门槛（matching.best_match 得分）
    RELEVANCE = {"personnel": 0.8, "qualifications": 0.6, "cases": 0.6}
    KIND_TITLES = {"personnel": "拟任团队人员", "qualifications": "资质证书", "cases": "类似项目业绩"}

    @staticmethod
    def _mark(item) -> str:
        return "（待核实：未经企业确认，正文中只能写作【待核实：…】）" if item.status == "unverified" else ""

    @staticmethod
    def _usable(items: list) -> list:
        """预设示例只用于展示录入格式，绝不进入撰写"""
        return [x for x in items if x.status != "example"]

    def asset_line(self, kind: str, x) -> str:
        """一条资料注入提示词的文本：只列已填写的字段；写明所属主体；待核实资料附带占位说明"""
        holder = f"所属主体：{x.holder}" if getattr(x, "holder", "") else ""
        if kind == "personnel":
            details = "，".join(v for v in [
                x.education,
                f"从业{x.years_of_experience}年" if x.years_of_experience is not None else "",
                f"职称：{x.professional_title}" if x.professional_title else "",
                f"持有证书：{'、'.join(x.certificates)}" if x.certificates else "",
                holder,
            ] if v)
            return (f"- **{x.role}**：{x.name}（{details or '履历未填写'}）{self._mark(x)}"
                    + (f"\n  简介：{x.intro}" if x.intro else ""))
        if kind == "qualifications":
            details = "，".join(v for v in [
                f"级别：{x.level}" if x.level else "",
                f"证书号：{x.cert_no}" if x.cert_no else "",
                f"发证机关：{x.issue_org}" if x.issue_org else "",
                f"有效期至：{x.expiry_date}" if x.expiry_date else "",
                holder,
            ] if v)
            return f"- **{x.name}**（{details}）{self._mark(x)}" + (f"\n  说明：{x.summary}" if x.summary else "")
        details = "，".join(v for v in [
            f"客户：{x.client_name}" if x.client_name else "",
            f"合同额：{x.contract_amount}" if x.contract_amount else "",
            f"签约时间：{x.sign_date}" if x.sign_date else "",
            f"验收结论：{x.acceptance_status}" if x.acceptance_status else "",
            holder,
        ] if v)
        return f"- **{x.project_name}**（{details}）{self._mark(x)}" + (f"\n  概述：{x.summary}" if x.summary else "")

    def _relevance(self, kind: str, x, text: str) -> float:
        if kind == "personnel":
            return best_match([x.role] + list(x.certificates) + [x.professional_title], text)[0]
        if kind == "qualifications":
            return best_match([x.name], text)[0]
        return best_match([x.project_name, x.contract_category] + list(x.key_deliverables), text)[0]

    def select_relevant(self, kind: str, items: list, text: str) -> list:
        """
        按与章节标题/要求的相关度挑选资料：有明确命中（岗位、证书名、资质名、业绩名）时只取命中的条目；
        泛指的团队/资质/业绩章节没有命中时取全部可用资料。最多 MAX_MATCHED 条。
        """
        scored = sorted(((self._relevance(kind, x, text), x) for x in items), key=lambda t: -t[0])
        hits = [x for s, x in scored if s >= self.RELEVANCE[kind]]
        return (hits or list(items))[: self.MAX_MATCHED]

    @staticmethod
    def _split_excluded(kind: str, items: list, exclude) -> Tuple[list, List[Dict[str, Any]]]:
        """按 exclude(kind, 资料字典) 拆成 (可用, 排除记录)；排除记录为 {kind, item, reason}"""
        if exclude is None:
            return list(items), []
        kept, dropped = [], []
        for x in items:
            reason = exclude(kind, x.model_dump())
            if reason:
                dropped.append({"kind": kind, "item": x.model_dump(), "reason": reason})
            else:
                kept.append(x)
        return kept, dropped

    @staticmethod
    def _excluded_note(label: str, count: int) -> str:
        return (f"【{label}】相关的 {count} 条企业资料因证书过期或所属主体与投标人不一致已排除，不得写入正文；"
                "相应内容按事实完备纪律处理，不得编造。")

    def linked_context(
        self, pairs: List[Tuple[str, Dict[str, Any]]], exclude=None,
    ) -> Tuple[str, List[Tuple[str, Any]], List[Dict[str, Any]]]:
        """
        本节评分项已关联的资料（用户确认）→ (提示词文本, [(kind, 资料模型)], 排除记录)。
        示例资料不进入撰写；exclude 判定为过期、主体不符的资料不进入提示词，只说明已排除。
        """
        models, excluded = [], []
        for kind, item in pairs:
            if item.get("status") == "example":
                continue
            kept, dropped = self._split_excluded(kind, [ASSET_MODELS[kind](**item)], exclude)
            models += [(kind, m) for m in kept]
            excluded += dropped
        if not models:
            return (self._excluded_note("本节评分项关联的企业资料", len(excluded)) if excluded else ""), [], excluded
        lines = ["【本节评分项关联的企业资料（用户确认关联；资质、人员、业绩等事实只能取自以下条目）】："]
        for kind in MATERIAL_KINDS:
            group = [m for k, m in models if k == kind]
            if group:
                lines.append(f"{self.KIND_TITLES[kind]}：")
                lines.extend(self.asset_line(kind, m) for m in group)
        return "\n".join(lines), models, excluded

    def match_assets_for_section(self, section_title: str, requirements: List[str], exclude=None) -> Dict[str, Any]:
        """
        根据当前撰写章节的主题匹配企业资料：只用用户录入的资料（排除预设示例），只取与本章相关的条目。
        团队/资质/业绩类章节没有可用资料时返回"未录入"说明，提示模型按事实完备纪律处理、不得编造。
        exclude(kind, 资料字典) 返回排除原因时（证书过期、主体不符），该条目不进入提示词：先按相关度选，再排除，
        相关条目全被排除时不改用其他条目，只说明已排除。
        返回 {type, kind, title, context_text, items, excluded}；kind 为资料类别（personnel / qualifications / cases / components）。
        """
        text = f"{section_title} {' '.join(requirements)}"
        matched = {
            "type": "none",
            "kind": "",
            "title": "",
            "context_text": "",
            "items": [],
            "excluded": [],
        }

        def fill(kind: str, legacy: str, title: str, items: list, missing: str):
            chosen = self.select_relevant(kind, items, text) if items else []
            kept, dropped = self._split_excluded(kind, chosen, exclude)
            matched.update(type=legacy, kind=kind, title=title, items=[x.model_dump() for x in kept], excluded=dropped)
            header = f"【{self.KIND_TITLES[kind]}（用户录入，相关事实只能取自以下条目）】："
            if kept:
                matched["context_text"] = "\n".join([header] + [self.asset_line(kind, x) for x in kept])
            else:
                matched["context_text"] = self._excluded_note(self.KIND_TITLES[kind], len(dropped)) if dropped else missing
            return matched

        # 1. 团队人员/组织架构章节
        if any(kw in text for kw in ["实施团队", "人员配置", "项目团队", "项目经理", "架构师", "技术人员",
                                     "人员资质", "人员要求", "团队成员", "项目负责人", "技术负责人"]):
            return fill("personnel", "personnel", "企业资料：拟任团队人员", self._usable(self.list_personnel()),
                        "【拟任团队人员】企业尚未录入人员资料：人员姓名、证书、从业年限按事实完备纪律处理，不得编造。")

        # 2. 资质资信/合规准入章节
        if any(kw in text for kw in ["资质", "准入", "CMMI", "ISO", "高新", "涉密", "信用"]):
            return fill("qualifications", "qualification", "企业资料：资质证书", self._usable(self.list_qualifications()),
                        "【资质证书】企业尚未录入资质资料：不得声称持有任何具体资质或证书编号，按事实完备纪律处理。")

        # 3. 类似业绩/成功案例章节
        if any(kw in text for kw in ["业绩", "案例", "项目经历", "类似项目", "成功案例"]):
            return fill("cases", "case", "企业资料：类似项目业绩", self._usable(self.list_cases()),
                        "【类似项目业绩】企业尚未录入业绩资料：不得编造项目名称、客户与合同金额，按事实完备纪律处理。")

        # 4. 技术方案组件匹配（按分类/标签）
        for comp in self._usable(self.list_components()):
            if comp.category in text or any(t in text for t in comp.tags):
                matched.update(type="component", kind="components", title=f"企业资料：方案组件 {comp.name}",
                               items=[comp.model_dump()])
                matched["context_text"] = f"【企业方案组件（用户录入）- {comp.name}】{self._mark(comp)}：\n{comp.content}"
                return matched

        return matched

    def get_stats(self) -> Dict[str, int]:
        return {
            "total_qualifications": len(self.qualifications),
            "total_personnel": len(self.personnel),
            "total_cases": len(self.cases),
            "total_components": len(self.components),
            "total_examples": sum(1 for kind in ASSET_KINDS for x in getattr(self, kind) if x.get("status") == "example"),
        }

    def check_materials(self, facts=None, deadline=None) -> List[MaterialCheck]:
        """资质、人员、业绩三类资料逐条做证明材料检查（见 material_check.check_material）"""
        with self._lock:
            snapshot = {kind: copy.deepcopy(getattr(self, kind)) for kind in MATERIAL_KINDS}
        return [check_material(kind, item, facts, deadline) for kind in MATERIAL_KINDS for item in snapshot[kind]]


asset_manager = EnterpriseAssetManager()
