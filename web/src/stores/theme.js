import { ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { theme } from 'ant-design-vue'

const FALLBACK_FONT =
  "-apple-system, BlinkMacSystemFont, 'Noto Sans SC', 'Roboto', 'HarmonyOS Sans SC', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"

/**
 * 读取 CSS 变量
 * Ant Design 需要真实色值才能派生 hover / active / focus 等状态，
 * 因此运行时从 base.css / base.dark.css 读取，保证与自定义样式单一来源。
 */
const readToken = (name, fallback) => {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

const buildTheme = (dark) => {
  const read = readToken
  const config = {
    token: {
      // 字体与排版
      fontFamily: read('--font-family-sans', FALLBACK_FONT),
      fontSize: 14,
      lineHeight: 1.6,

      // 品牌主色：填充面与文字态分开，保证前景文字对比度
      colorPrimary: read('--primary-fill', '#046a82'),
      colorPrimaryHover: read('--primary-fill-hover', '#24839a'),
      colorPrimaryActive: read('--primary-fill-active', '#035065'),
      colorPrimaryBorder: read('--primary-fill', '#046a82'),
      colorLink: read('--primary-text', '#046a82'),
      colorLinkHover: read('--primary-text-hover', '#24839a'),
      colorLinkActive: read('--primary-text-active', '#035065'),

      // 背景层级
      colorBgBase: read('--color-bg-container', '#ffffff'),
      colorBgContainer: read('--color-bg-container', '#ffffff'),
      colorBgElevated: read('--color-bg-elevated', '#fafcfd'),
      colorBgLayout: read('--color-bg-page', '#ffffff'),

      // 文本层级
      colorText: read('--color-text', '#151616'),
      colorTextSecondary: read('--color-text-secondary', '#697070'),
      colorTextTertiary: read('--color-text-tertiary', '#979999'),
      colorTextQuaternary: read('--color-text-tertiary', '#979999'),
      colorTextPlaceholder: read('--color-text-placeholder', '#bdbfbf'),
      colorTextDisabled: read('--state-disabled-text', '#bdbfbf'),

      // 边框与填充
      colorBorder: read('--color-border', '#e4e6e6'),
      colorBorderSecondary: read('--color-border-subtle', '#eef0f0'),
      colorFill: read('--color-fill-hover', '#f8fafa'),
      colorFillSecondary: read('--color-fill-hover', '#f8fafa'),
      colorFillTertiary: read('--color-fill-active', '#f5f7f7'),
      colorFillQuaternary: read('--color-fill-disabled', '#eff2f2'),

      // 语义状态色
      colorSuccess: read('--color-success-500', '#52c41a'),
      colorError: read('--color-error-500', '#ff4d4f'),
      colorWarning: read('--color-warning-500', '#faad14'),
      colorInfo: read('--color-info-500', '#1890ff'),
      colorSuccessBg: read('--color-success-50', '#e6f7e6'),
      colorSuccessBorder: read('--color-success-100', '#b7eb8f'),
      colorErrorBg: read('--color-error-50', '#fff2f0'),
      colorErrorBorder: read('--color-error-100', '#ffccc7'),
      colorWarningBg: read('--color-warning-50', '#fffbe6'),
      colorWarningBorder: read('--color-warning-100', '#ffe58f'),
      colorInfoBg: read('--color-info-50', '#e6f7ff'),
      colorInfoBorder: read('--color-info-100', '#bae7ff'),

      // 圆角与动效
      borderRadius: 8,
      borderRadiusLG: 8,
      borderRadiusSM: 6,
      borderRadiusXS: 4,
      motionDurationFast: '0.12s',
      motionDurationMid: '0.18s',
      motionDurationSlow: '0.26s',
      wireframe: false
    }
  }

  if (dark) {
    config.algorithm = theme.darkAlgorithm
  }

  return config
}

export const useThemeStore = defineStore('theme', () => {
  // 从 localStorage 读取保存的主题；未保存时检测系统暗色模式偏好
  const savedTheme = localStorage.getItem('theme')
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  const isDark = ref(savedTheme ? savedTheme === 'dark' : prefersDark)

  // 当前主题配置（浅色 / 深色共用同一份 token 定义，仅算法不同）
  const currentTheme = shallowRef(buildTheme(isDark.value))

  // 切换主题
  function toggleTheme() {
    setTheme(!isDark.value)
  }

  // 设置主题
  function setTheme(dark) {
    isDark.value = dark
    localStorage.setItem('theme', dark ? 'dark' : 'light')
    // 先切换 .dark 让 CSS 变量生效，再按新变量重建 Ant Design 主题
    updateDocumentTheme()
    currentTheme.value = buildTheme(dark)
  }

  // 样式表就绪后重新读取一次变量，避免首屏拿到回退色值
  function refreshTheme() {
    currentTheme.value = buildTheme(isDark.value)
  }

  // 更新 document 的主题类
  function updateDocumentTheme() {
    if (isDark.value) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }

  // 初始化时设置主题
  updateDocumentTheme()

  return {
    isDark,
    currentTheme,
    toggleTheme,
    setTheme,
    refreshTheme
  }
})
