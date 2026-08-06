import { apiGet, apiRequest } from './base.js'

export const notificationApi = {
  list: (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
    })
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return apiGet(`/api/notifications${suffix}`)
  },

  markRead: (notificationId) => apiRequest(
    `/api/notifications/${encodeURIComponent(notificationId)}/read`,
    { method: 'POST' }
  )
}

export default notificationApi
