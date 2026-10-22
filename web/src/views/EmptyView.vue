<template>
  <div class="not-found">
    <h1>404 - 页面不存在</h1>
    <p>你访问的地址不可用，可能是链接已失效或输入有误。</p>
    <router-link class="not-found-link" to="/research">返回科研罗盘</router-link>
  </div>
</template>

<script setup></script>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 80vh;
  text-align: center;
}

.not-found h1 {
  font-size: 2rem;
  margin-bottom: 1rem;
}

.not-found p {
  font-size: 1.5rem;
  margin-bottom: 2rem;
}

.not-found-link {
  font-size: 1rem;
  color: var(--primary-text);
  text-decoration: none;
}

.not-found-link:hover {
  color: var(--primary-text-hover);
  text-decoration: underline;
}
</style>
