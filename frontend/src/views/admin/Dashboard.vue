<template>
  <div class="admin-layout">
    <aside class="admin-sidebar">
      <div class="logo" @click="$router.push('/')">🔬 Deep Research</div>
      <nav>
        <router-link to="/admin" class="nav-item active">📊 仪表盘</router-link>
        <router-link to="/admin/users" class="nav-item">👥 用户管理</router-link>
      </nav>
      <div class="back-link" @click="$router.push('/')">← 返回研究</div>
    </aside>

    <main class="admin-main">
      <h1>仪表盘</h1>

      <div class="cards">
        <div class="card primary">
          <div class="card-value">{{ data.totalStudies ?? '-' }}</div>
          <div class="card-label">累计研究次数</div>
          <div class="card-sub">今日 {{ data.todayStudies ?? 0 }}</div>
        </div>
        <div class="card success">
          <div class="card-value">{{ data.activeSessions ?? '-' }}</div>
          <div class="card-label">活跃会话</div>
        </div>
        <div class="card warning">
          <div class="card-value">{{ data.errorRate ?? 0 }}%</div>
          <div class="card-label">累计失败率</div>
        </div>
        <div class="card info">
          <div class="card-value">{{ data.totalUsers ?? '-' }}</div>
          <div class="card-label">注册用户</div>
        </div>
        <div class="card accent">
          <div class="card-value">{{ fmtTokens(data.tokenStats?.totalTokens ?? 0) }}</div>
          <div class="card-label">累计 Token 消耗</div>
          <div class="card-sub">输入 {{ fmtTokens(data.tokenStats?.totalPromptTokens ?? 0) }} / 输出 {{ fmtTokens(data.tokenStats?.totalCompletionTokens ?? 0) }}</div>
        </div>
      </div>

      <!-- 每日研究次数柱状图 -->
      <div class="panel">
        <div class="panel-header">
          <h2>研究次数趋势</h2>
          <div class="tabs">
            <button :class="['tab', { active: range === 7 }]" @click="range = 7">近 7 天</button>
            <button :class="['tab', { active: range === 30 }]" @click="range = 30">近 30 天</button>
          </div>
        </div>
        <Bar v-if="range === 7 && chartData7" :data="chartData7" :options="barOptions" />
        <Bar v-if="range === 30 && chartData30" :data="chartData30" :options="barOptions" />
      </div>

      <!-- Token 趋势 + 用户统计 并排 -->
      <div class="cols">
        <div class="panel col">
          <h2>Token 消耗趋势（近 30 天）</h2>
          <Bar v-if="tokenChartData" :data="tokenChartData" :options="barOptions" />
          <p v-else class="empty-tip">暂无数据</p>
        </div>
        <div class="panel col">
          <h2>用户研究次数 Top 10</h2>
          <Bar v-if="userChartData" :data="userChartData" :options="horizBarOptions" />
          <p v-else class="empty-tip">暂无数据</p>
        </div>
      </div>

      <!-- 状态分布 -->
      <div class="panel" v-if="data.byStatus">
        <h2>研究状态分布</h2>
        <div class="doughnut-wrap">
          <Doughnut v-if="statusChartData" :data="statusChartData" :options="doughnutOptions" />
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../../utils/api'
import { Bar, Doughnut } from 'vue-chartjs'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, ArcElement, Title, Tooltip, Legend } from 'chart.js'

ChartJS.register(CategoryScale, LinearScale, BarElement, ArcElement, Title, Tooltip, Legend)

const data = ref({})
const range = ref(7)

function fmtTokens(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

const barOptions = { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } }
const horizBarOptions = { responsive: true, indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, ticks: { stepSize: 1 } } } }
const doughnutOptions = { responsive: true, plugins: { legend: { position: 'bottom' } } }

const chartData7 = computed(() => makeBarData(data.value?.dailyCounts7 ?? [], 'count', 'research7'))
const chartData30 = computed(() => makeBarData(data.value?.dailyCounts30 ?? [], 'count', 'research30'))
const tokenChartData = computed(() => makeBarData(data.value?.dailyTokens30 ?? [], 'tokens', 'tokens'))
const userChartData = computed(() => ({
  labels: (data.value?.userStats ?? []).slice(0, 10).map(u => u.username).reverse(),
  datasets: [{ data: (data.value?.userStats ?? []).slice(0, 10).map(u => u.totalCount).reverse(), backgroundColor: '#818cf8' }]
}))
const statusChartData = computed(() => ({
  labels: Object.keys(data.value?.byStatus ?? {}),
  datasets: [{ data: Object.values(data.value?.byStatus ?? {}), backgroundColor: ['#60a5fa', '#34d399', '#f87171'] }]
}))

function makeBarData(items, key, label) {
  if (!items?.length) return null
  return { labels: items.map(i => i.date?.slice(5)), datasets: [{ label, data: items.map(i => i[key]), backgroundColor: '#818cf8' }] }
}

onMounted(async () => {
  try { const res = await api.get('/admin/dashboard'); data.value = res.data } catch (e) { console.error(e) }
})
</script>

<style scoped>
* { margin: 0; padding: 0; box-sizing: border-box; }
.admin-layout { display: flex; min-height: 100vh; background: #f7fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }

.admin-sidebar { width: 220px; background: #1e1b4b; color: #c7d2fe; display: flex; flex-direction: column; flex-shrink: 0; }
.logo { padding: 24px 20px 20px; font-size: 16px; font-weight: 700; color: #e0e7ff; cursor: pointer; user-select: none; }
nav { flex: 1; padding: 0 12px; }
.nav-item { display: block; padding: 10px 12px; color: #a5b4fc; text-decoration: none; border-radius: 6px; margin-bottom: 4px; font-size: 14px; }
.nav-item:hover { background: rgba(255,255,255,.08); color: #e0e7ff; }
.nav-item.router-link-active { background: rgba(255,255,255,.12); color: #fff; }
.back-link { padding: 16px 20px; color: #6b7280; font-size: 13px; cursor: pointer; border-top: 1px solid rgba(255,255,255,.06); }
.back-link:hover { color: #a5b4fc; }

.admin-main { flex: 1; padding: 40px 48px; overflow-y: auto; }
.admin-main h1 { font-size: 24px; font-weight: 700; color: #1a202c; margin-bottom: 28px; }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card { background: #fff; border-radius: 10px; padding: 22px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.card-value { font-size: 28px; font-weight: 700; }
.card-label { font-size: 13px; color: #718096; margin-top: 4px; }
.card-sub { font-size: 11px; color: #a0aec0; margin-top: 2px; }
.card.primary .card-value { color: #4f46e5; }
.card.success .card-value { color: #059669; }
.card.warning .card-value { color: #d97706; }
.card.info .card-value { color: #2563eb; }
.card.accent .card-value { color: #7c3aed; }

.panel { background: #fff; border-radius: 10px; padding: 24px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.panel h2 { font-size: 15px; font-weight: 600; color: #1a202c; margin-bottom: 16px; }
.panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.panel-header h2 { margin-bottom: 0; }
.tabs { display: flex; gap: 4px; background: #f1f5f9; border-radius: 8px; padding: 3px; }
.tab { padding: 5px 14px; border: none; background: none; border-radius: 6px; font-size: 12px; cursor: pointer; color: #64748b; }
.tab.active { background: #fff; color: #4f46e5; font-weight: 500; box-shadow: 0 1px 2px rgba(0,0,0,.06); }

.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.col { margin-bottom: 0; }

.doughnut-wrap { max-width: 260px; }
.empty-tip { color: #94a3b8; text-align: center; padding: 24px; }
</style>
