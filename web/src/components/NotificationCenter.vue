<template>
  <a-popover v-model:open="open" placement="bottomRight" trigger="click" overlay-class-name="notification-popover">
    <template #content>
      <section class="notification-panel" aria-label="通知中心">
        <header class="notification-header">
          <strong>通知中心</strong>
          <a-button type="link" size="small" :loading="loading" @click="load">刷新</a-button>
        </header>
        <a-spin v-if="loading && !items.length" />
        <a-empty v-else-if="!items.length" description="暂无通知" />
        <div v-else class="notification-list">
          <button
            v-for="item in items"
            :key="item.notification_id"
            type="button"
            class="notification-item"
            :class="{ unread: !item.read_at }"
            @click="read(item)"
          >
            <span class="notification-item-title">{{ item.title }}</span>
            <span class="notification-item-message">{{ item.message }}</span>
            <time>{{ formatTime(item.created_at) }}</time>
          </button>
        </div>
      </section>
    </template>
    <a-badge :count="unreadCount" :overflow-count="99" size="small">
      <button type="button" class="notification-trigger" aria-label="通知中心" @click="open = true">
        <Bell :size="17" />
      </button>
    </a-badge>
  </a-popover>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Bell } from 'lucide-vue-next'
import { notificationApi } from '@/apis/notification_api'

const open = ref(false)
const loading = ref(false)
const items = ref([])
let pollTimer

const unreadCount = computed(() => items.value.filter((item) => !item.read_at).length)

const load = async () => {
  loading.value = true
  try {
    const result = await notificationApi.list({ limit: 50 })
    items.value = result.items || []
  } catch (error) {
    if (open.value) message.error(error.message || '通知加载失败')
  } finally {
    loading.value = false
  }
}

const read = async (item) => {
  if (item.read_at) return
  try {
    await notificationApi.markRead(item.notification_id)
    item.read_at = new Date().toISOString()
  } catch (error) {
    message.error(error.message || '通知标记失败')
  }
}

const formatTime = (value) => {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

watch(open, (value) => { if (value) void load() })
onMounted(() => {
  void load()
  pollTimer = window.setInterval(() => { void load() }, 30_000)
})
onBeforeUnmount(() => window.clearInterval(pollTimer))
</script>

<style scoped lang="less">
.notification-trigger { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; padding: 0; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.notification-trigger:hover { background: var(--gray-100); color: var(--color-text); }
.notification-panel { width: min(360px, calc(100vw - 32px)); }
.notification-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.notification-list { max-height: 360px; overflow: auto; }
.notification-item { display: flex; flex-direction: column; gap: 3px; width: 100%; padding: 10px; border: 0; border-bottom: 1px solid var(--gray-100); background: transparent; text-align: left; cursor: pointer; }
.notification-item:hover { background: var(--gray-25); }
.notification-item.unread { border-left: 3px solid var(--main-color); }
.notification-item-title { color: var(--color-text); font-size: 13px; font-weight: 600; }
.notification-item-message { color: var(--color-text-secondary); font-size: 12px; line-height: 1.45; }
.notification-item time { color: var(--color-text-tertiary); font-size: 10px; }
</style>
