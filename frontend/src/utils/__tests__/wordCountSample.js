// 前后端字数口径一致：tests/test_text_stats.py 读取本文件的 SAMPLE 与 SAMPLE_BODY_CHARS 做同样的断言
export const SAMPLE = `**1.1 运维组织**

本项目设立运维组。

- 项目经理 1 名
+ 技术负责人

> 引用说明

| 岗位 | 人数 |
| :--- | ---: |
| 工程师 | 【待填写】 |

\`\`\`mermaid
graph TD
  A[服务台] --> B[二线]
\`\`\`

采用 \`ITIL\` 流程。`
export const SAMPLE_BODY_CHARS = 52
