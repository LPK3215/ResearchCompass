<template>
  <div class="register-view">
    <!-- 顶部导航：品牌名称 -->
    <nav class="register-navbar">
      <div class="navbar-content">
        <div class="brand-container" @click="goLogin" style="cursor: pointer">
          <img v-if="brandLogo" :src="brandLogo" alt="logo" class="brand-logo" />
          <h1 class="brand-text">
            <span v-if="brandOrgName" class="brand-org">{{ brandOrgName }}</span>
            <span v-if="brandOrgName && brandName !== brandOrgName" class="brand-separator"></span>
            <span class="brand-main">{{ brandName }}</span>
          </h1>
        </div>
        <div class="navbar-actions">
          <span class="nav-hint">已有账号？</span>
          <a-button type="link" size="small" @click="goLogin">立即登录</a-button>
        </div>
      </div>
    </nav>

    <!-- 主要内容区：居中卡片 -->
    <main class="register-main">
      <div class="register-card">
        <!-- 左侧图片 -->
        <div class="card-side is-image">
          <img :src="loginBgImage" alt="注册背景" class="register-bg-image" />
        </div>

        <!-- 右侧表单 -->
        <div class="card-side is-form">
          <div class="form-wrapper">
            <header class="form-header">
              <p class="welcome-text">创建你的账号</p>
              <p class="welcome-subtext">加入 {{ brandName }}，开启智能科研之旅</p>
            </header>

            <div class="register-form">
              <a-form :model="registerForm" @finish="handleRegister" layout="vertical">
                <a-form-item
                  label="用户名"
                  name="username"
                  :rules="[
                    { required: true, message: '请输入用户名' },
                    { pattern: /^[a-zA-Z0-9_\u4e00-\u9fa5]{2,20}$/, message: '用户名仅支持中英文、数字、下划线，2-20 个字符' }
                  ]"
                >
                  <a-input v-model:value="registerForm.username" placeholder="2-20 个字符，中英文/数字/下划线">
                    <template #prefix>
                      <user-icon size="18" />
                    </template>
                  </a-input>
                </a-form-item>

                <a-form-item
                  label="密码"
                  name="password"
                  :rules="[
                    { required: true, message: '请输入密码' },
                    { min: 8, message: '密码至少 8 位' }
                  ]"
                >
                  <a-input-password v-model:value="registerForm.password" placeholder="至少 8 位">
                    <template #prefix>
                      <lock-icon size="18" />
                    </template>
                  </a-input-password>
                </a-form-item>

                <a-form-item
                  label="确认密码"
                  name="confirmPassword"
                  :rules="[
                    { required: true, message: '请再次输入密码' },
                    { validator: validateConfirmPassword }
                  ]"
                >
                  <a-input-password v-model:value="registerForm.confirmPassword" placeholder="再次输入密码">
                    <template #prefix>
                      <lock-icon size="18" />
                    </template>
                  </a-input-password>
                </a-form-item>

                <a-form-item
                  label="手机号（可选）"
                  name="phone_number"
                  :rules="[
                    { pattern: /^1[3-9]\d{9}$/, message: '请输入有效的手机号' }
                  ]"
                >
                  <a-input v-model:value="registerForm.phone_number" placeholder="可用于登录和找回密码">
                    <template #prefix>
                      <phone-icon size="18" />
                    </template>
                  </a-input>
                </a-form-item>

                <a-form-item v-if="showAgreementConsent" class="agreement-form-item">
                  <div class="agreement-row">
                    <a-checkbox v-model:checked="agreementAccepted">
                      注册即代表同意
                      <a class="agreement-link" :href="userAgreementUrl" target="_blank" rel="noopener noreferrer" @click.stop>《用户协议》</a>
                      <a class="agreement-link" :href="privacyPolicyUrl" target="_blank" rel="noopener noreferrer" @click.stop>《隐私协议》</a>
                    </a-checkbox>
                  </div>
                </a-form-item>

                <a-form-item>
                  <a-button
                    type="primary"
                    html-type="submit"
                    :loading="loading"
                    :disabled="!canSubmit"
                    block
                    size="large"
                  >
                    注册
                  </a-button>
                </a-form-item>
              </a-form>

              <div v-if="registerResult" class="register-success">
                <a-alert
                  type="success"
                  show-icon
                  :message="`注册成功！你的登录 UID 是：${registerResult.uid}`"
                  description="请使用 UID 或手机号登录。即将跳转到登录页..."
                />
              </div>

              <div class="form-footer">
                <span>遇到问题？</span>
                <a href="https://github.com/LPK3215/ResearchCompass/issues" target="_blank">联系支持</a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- 底部 -->
    <footer class="register-footer">
      <div class="footer-links">
        <a href="https://github.com/LPK3215/ResearchCompass/issues" target="_blank">问题反馈</a>
        <span class="divider">|</span>
        <a href="https://lpk3215.github.io/ResearchCompass/" target="_blank">使用帮助</a>
      </div>
      <div class="copyright">
        &copy; {{ new Date().getFullYear() }} {{ brandName }}. All Rights Reserved.
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useInfoStore } from '@/stores/info'
import { authApi } from '@/apis/auth_api'
import {
  User as UserIcon,
  Lock as LockIcon,
  Phone as PhoneIcon
} from 'lucide-vue-next'

const router = useRouter()
const infoStore = useInfoStore()

const brandName = computed(() => infoStore.organization?.name || 'ResearchCompass')
const brandOrgName = computed(() => infoStore.organization?.name || '')
const brandLogo = computed(() => infoStore.organization?.logo || '/favicon.svg')
const loginBgImage = computed(() => infoStore.organization?.login_bg || '/login-bg.svg')
const userAgreementUrl = computed(() => infoStore.footer?.user_agreement_url?.trim() || '')
const privacyPolicyUrl = computed(() => infoStore.footer?.privacy_policy_url?.trim() || '')
const showAgreementConsent = computed(() => Boolean(userAgreementUrl.value && privacyPolicyUrl.value))

const loading = ref(false)
const agreementAccepted = ref(false)
const registerResult = ref(null)

const registerForm = reactive({
  username: '',
  password: '',
  confirmPassword: '',
  phone_number: ''
})

const canSubmit = computed(() => {
  if (!registerForm.username || !registerForm.password || !registerForm.confirmPassword) {
    return false
  }
  if (registerForm.password !== registerForm.confirmPassword) {
    return false
  }
  if (showAgreementConsent.value && !agreementAccepted.value) {
    return false
  }
  return true
})

const validateConfirmPassword = async (_rule, value) => {
  if (value !== registerForm.password) {
    throw new Error('两次输入的密码不一致')
  }
}

const goLogin = () => {
  router.push('/login')
}

const handleRegister = async () => {
  if (!canSubmit.value) {
    if (showAgreementConsent.value && !agreementAccepted.value) {
      message.warning('请先阅读并同意《用户协议》《隐私协议》')
    }
    return
  }

  try {
    loading.value = true
    const result = await authApi.register({
      username: registerForm.username,
      password: registerForm.password,
      phone_number: registerForm.phone_number || null
    })
    registerResult.value = result
    message.success('注册成功')
    // 3 秒后跳转登录页
    setTimeout(() => {
      router.push({ path: '/login', query: { uid: result.uid } })
    }, 3000)
  } catch (error) {
    message.error(error.message || '注册失败，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="less">
.register-view {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(135deg, #023944 0%, #01151f 100%);
  color: #fff;
}

.register-navbar {
  padding: 16px 32px;
  background: rgba(0, 0, 0, 0.2);
  backdrop-filter: blur(10px);

  .navbar-content {
    max-width: 1200px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .brand-container {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .brand-logo {
    width: 36px;
    height: 36px;
    border-radius: 8px;
  }

  .brand-text {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    color: #fff;
    display: flex;
    align-items: center;
    gap: 8px;

    .brand-separator {
      width: 1px;
      height: 16px;
      background: rgba(255, 255, 255, 0.3);
    }

    .brand-main {
      color: #a3d8e8;
    }
  }

  .navbar-actions {
    display: flex;
    align-items: center;
    gap: 8px;

    .nav-hint {
      color: rgba(255, 255, 255, 0.7);
      font-size: 14px;
    }
  }
}

.register-main {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.register-card {
  display: flex;
  width: min(960px, 100%);
  height: min(620px, 80vh);
  background: #fff;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
}

.card-side {
  flex: 1;

  &.is-image {
    background: #023944;

    .register-bg-image {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
  }

  &.is-form {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 40px;

    .form-wrapper {
      width: 100%;
      max-width: 360px;
    }
  }
}

.form-header {
  margin-bottom: 24px;

  .welcome-text {
    font-size: 24px;
    font-weight: 600;
    color: #1f2937;
    margin: 0 0 8px;
  }

  .welcome-subtext {
    font-size: 14px;
    color: #6b7280;
    margin: 0;
  }
}

.agreement-form-item {
  margin-bottom: 16px;

  .agreement-row {
    font-size: 13px;
    color: #4b5563;
  }

  .agreement-link {
    color: #046a82;
    margin: 0 2px;

    &:hover {
      text-decoration: underline;
    }
  }
}

.register-success {
  margin: 16px 0;
}

.form-footer {
  margin-top: 16px;
  text-align: center;
  font-size: 13px;
  color: #6b7280;

  a {
    color: #046a82;
    margin-left: 4px;

    &:hover {
      text-decoration: underline;
    }
  }
}

.register-footer {
  padding: 16px 32px;
  text-align: center;
  color: rgba(255, 255, 255, 0.6);
  font-size: 13px;

  .footer-links {
    margin-bottom: 8px;

    a {
      color: rgba(255, 255, 255, 0.8);
      margin: 0 8px;

      &:hover {
        color: #fff;
      }
    }

    .divider {
      color: rgba(255, 255, 255, 0.3);
    }
  }
}

@media (max-width: 768px) {
  .register-card {
    flex-direction: column;
    height: auto;
    max-height: none;
  }

  .card-side.is-image {
    display: none;
  }

  .card-side.is-form {
    padding: 24px;
  }
}
</style>
