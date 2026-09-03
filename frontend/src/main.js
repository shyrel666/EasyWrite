import { createApp } from 'vue'
import App from './App.vue'
import mermaid from 'mermaid'

mermaid.initialize({ startOnLoad: false, theme: 'neutral' })

createApp(App).mount('#app')
