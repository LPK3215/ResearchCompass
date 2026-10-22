<script setup>
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { onMounted } from 'vue'

const agentStore = useAgentStore()
const userStore = useUserStore()
const themeStore = useThemeStore()

onMounted(async () => {
  // 样式表就绪后重新读取一次 CSS 变量，确保 Ant Design 主题与设计令牌一致
  themeStore.refreshTheme()
  if (userStore.isLoggedIn) {
    await agentStore.initialize()
  }
})
</script>
<template>
  <a-config-provider :theme="themeStore.currentTheme" :locale="zhCN">
    <router-view />
  </a-config-provider>
</template>
