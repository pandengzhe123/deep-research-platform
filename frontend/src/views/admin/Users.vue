<template>
  <div class="admin-layout">
    <aside class="admin-sidebar">
      <div class="logo" @click="$router.push('/')">🔬 Deep Research</div>
      <nav>
        <router-link to="/admin" class="nav-item">📊 仪表盘</router-link>
        <router-link to="/admin/users" class="nav-item active">👥 用户管理</router-link>
      </nav>
      <div class="back-link" @click="$router.push('/')">← 返回研究</div>
    </aside>

    <main class="admin-main">
      <h1>用户管理</h1>

      <div class="panel">
        <table v-if="users.length">
          <thead>
            <tr>
              <th>ID</th><th>用户名</th><th>角色</th><th>研究次数</th>
              <th>状态</th><th>注册时间</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td class="muted">#{{ u.id }}</td>
              <td class="fw">{{ u.username }}</td>
              <td>
                <select :value="u.role" @change="changeRole(u.id, $event.target.value)" class="role-select">
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td>{{ u.researchCount }}</td>
              <td>
                <span :class="['badge', u.enabled ? 'enabled' : 'disabled']">
                  {{ u.enabled ? '启用' : '禁用' }}
                </span>
              </td>
              <td class="muted">{{ formatDate(u.createdAt) }}</td>
              <td>
                <button
                  :class="['btn', u.enabled ? 'btn-danger' : 'btn-success']"
                  @click="toggleUser(u.id, !u.enabled)"
                >
                  {{ u.enabled ? '禁用' : '启用' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty">加载中...</p>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../../utils/api'

const users = ref([])

onMounted(loadUsers)

async function loadUsers() {
  try {
    const res = await api.get('/admin/users')
    users.value = res.data
  } catch (e) {
    console.error('加载用户列表失败', e)
  }
}

async function toggleUser(id, enabled) {
  try {
    await api.put(`/admin/users/${id}/status`, { enabled })
    await loadUsers()
  } catch (e) {
    console.error('操作失败', e)
  }
}

async function changeRole(id, role) {
  try {
    await api.put(`/admin/users/${id}/role`, { role })
    await loadUsers()
  } catch (e) {
    console.error('修改角色失败', e)
  }
}

function formatDate(d) {
  if (!d) return '-'
  return new Date(d).toLocaleDateString('zh-CN')
}
</script>

<style scoped>
* { margin: 0; padding: 0; box-sizing: border-box; }
.admin-layout { display: flex; min-height: 100vh; background: #f7fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }

.admin-sidebar {
  width: 220px; background: #1e1b4b; color: #c7d2fe; display: flex; flex-direction: column; flex-shrink: 0;
}
.logo { padding: 24px 20px 20px; font-size: 16px; font-weight: 700; color: #e0e7ff; cursor: pointer; user-select: none; }
nav { flex: 1; padding: 0 12px; }
.nav-item { display: block; padding: 10px 12px; color: #a5b4fc; text-decoration: none; border-radius: 6px; margin-bottom: 4px; font-size: 14px; }
.nav-item:hover { background: rgba(255,255,255,.08); color: #e0e7ff; }
.nav-item.router-link-active { background: rgba(255,255,255,.12); color: #fff; }
.back-link { padding: 16px 20px; color: #6b7280; font-size: 13px; cursor: pointer; border-top: 1px solid rgba(255,255,255,.06); }
.back-link:hover { color: #a5b4fc; }

.admin-main { flex: 1; padding: 40px 48px; overflow-y: auto; }
.admin-main h1 { font-size: 24px; font-weight: 700; color: #1a202c; margin-bottom: 28px; }

.panel { background: #fff; border-radius: 10px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 12px 14px; border-bottom: 1px solid #f1f5f9; font-size: 13px; }
th { color: #94a3b8; font-weight: 500; text-transform: uppercase; font-size: 11px; letter-spacing: .5px; }
.fw { font-weight: 500; color: #1a202c; }
.muted { color: #94a3b8; }

.badge { padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: 500; }
.badge.enabled { background: #d1fae5; color: #065f46; }
.badge.disabled { background: #fee2e2; color: #991b1b; }

.role-select { padding: 4px 10px; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 12px; background: #fff; }

.btn { padding: 5px 14px; border: none; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer; color: #fff; }
.btn-danger { background: #ef4444; }
.btn-danger:hover { background: #dc2626; }
.btn-success { background: #10b981; }
.btn-success:hover { background: #059669; }

.empty { color: #94a3b8; text-align: center; padding: 32px; }
</style>
