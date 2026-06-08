# 前端开发路线图

> 当前状态：单个 HTML 文件（179 行），原生 JS，功能完整但体验粗糙。

---

## 一、当前状态评估

### 已实现

| 功能 | 状态 | 说明 |
|------|:--:|------|
| 问题输入 + Level 选择 | ✅ | 下拉切换 1-4 |
| 计时器 | ✅ | 提交后实时显示运行时间 |
| Markdown 渲染 | ✅ | 基础转换（标题/链接/粗体） |
| RAG 复选框 | ✅ | 勾选后 Agent 可用知识库 |
| KB 文件上传 | ✅ | PDF/TXT/MD，调 Python |
| KB 文件列表 + 删除 | ✅ | 按用户隔离 |
| 登录/注册 | ✅ | JWT + localStorage |
| 退出 | ✅ | 清除 token + 刷新列表 |
| 会话列表 | ✅ | 按用户过滤，显示问题标题 |
| 追问上下文 | ✅ | contextHistory 累积 |
| 错误提示 | ✅ | 网络错误/认证失败提示 |

### 当前痛点

| 问题 | 严重度 | 说明 |
|------|--------|------|
| 登录和主界面挤在一起 | 高 | 没有路由，所有功能堆在一个页面 |
| 标记着"已登录"就没了 | 中 | 没有用户中心、看不到自己上传了多少文件 |
| 会话列表只显示 ID | 中 | 已改，但交互太简陋——不能展开看对话 |
| 移动端不可用 | 中 | 没有响应式布局，手机上看挤成一片 |
| 没有加载/空状态设计 | 低 | 等待中、无数据、出错三种状态没区分 |
| 手动刷新才能看结果 | 低 | 报告出来没有自动滚动到底部 |

---

## 二、技术选型

| 选项 | 适合度 | 说明 |
|------|--------|------|
| **Vue 3 + Vite**（推荐） | ⭐⭐⭐ | 国内主流，中文文档好，上手快，生态完善 |
| React + Vite | ⭐⭐ | 同样可行，生态更大但学习曲线陡一点 |
| Svelte | ⭐ | 简洁但国内岗位少 |
| 保持原生 HTML | ⭐⭐ | 当前方案，179 行够用——但如果要加路由/状态管理会很痛苦 |

**决策**：如果目标是校招简历——Vue 3 最安全。如果是快速演示——当前 HTML 够用，和简历上 Agent/Java 理解度相比前端框架不重要。

---

## 三、两个路线

### 路线 A：Vue 3 SPA（推荐，3-4 天）

```
前端项目结构：
frontend/
├── index.html
├── package.json
├── vite.config.js
├── src/
│   ├── main.js
│   ├── App.vue                    ← 路由容器
│   ├── router/
│   │   └── index.js               ← 登录页 / 研究页 / 历史页
│   ├── views/
│   │   ├── LoginView.vue          ← 登录/注册
│   │   ├── ResearchView.vue       ← 主研究界面
│   │   └── SessionsView.vue       ← 历史会话
│   ├── components/
│   │   ├── QuestionInput.vue      ← 输入框 + Level 选择
│   │   ├── ResearchPanel.vue      ← 提交按钮 + 计时器 + 进度
│   │   ├── ReportRenderer.vue     ← Markdown → HTML 渲染
│   │   ├── KBPanel.vue            ← KB 上传/列表/删除
│   │   └── SessionList.vue        ← 会话列表
│   ├── stores/
│   │   └── auth.js                ← Pinia: token/user 状态管理
│   └── utils/
│       └── api.js                 ← axios 封装 + 拦截器 + 自动带 JWT
```

### 分步

#### Step 1：项目骨架（30 分钟）

- `npm create vite@latest frontend -- --template vue`
- 装依赖：`vue-router` + `pinia` + `axios` + `marked`
- 配 Vite proxy：`/api` → `localhost:8080`（不再跨域了）

#### Step 2：登录页（30 分钟）

- LoginView.vue：登录/注册表单
- Pinia auth store：存 token + username，localStorage 持久化
- axios 拦截器：自动带 `Authorization: Bearer <token>`
- 路由守卫：未登录 → 跳到登录页

#### Step 3：研究主界面（1 小时）

- ResearchView.vue：把现有 HTML 的功能搬到 Vue
- 组件拆分：QuestionInput / ResearchPanel / ReportRenderer
- 进度动画：计时器 + 状态指示

#### Step 4：KB 面板 + 会话列表（1 小时）

- KBPanel.vue：上传/列表/删除
- SessionList.vue：会话历史 + 点击继续追问

#### Step 5：打磨（1 小时）

- 响应式布局（手机可用）
- 加载/空/错误三种状态
- 报告自动滚动到底部

### 路线 B：保持原生 HTML 优化（1-2 天）

只在当前 `index.html` 上做轻量改进：

| 改动 | 行数 | 效果 |
|------|------|------|
| 登录/主界面分两个容器，切换显示 | ~15 | 视觉上分离了 |
| 会话列表支持点击展开对话 | ~20 | 能看到之前问答 |
| 加 CSS 响应式布局 | ~20 | 手机可用 |
| 报告完成后自动滚动 | ~5 | 不用手动滑 |
| 加一个"新建会话"按钮 | ~10 | 从空白开始 |

---

## 四、决策建议

| 目标 | 选哪个 |
|------|--------|
| 校招面试、简历上有前端框架经验 | **路线 A**——Vue 3 是安全牌 |
| 快速演示、不在乎前端 | **路线 B**——1 天打磨现有 HTML |
| 纯展示 Agent 能力、不当前端开发者 | 当前 HTML 够用——不做改动直接推也行 |

---

## 五、接口清单（前后端已就绪）

| 接口 | 方法 | 说明 | 需要 JWT |
|------|------|------|---------|
| `/api/auth/register` | POST | 注册 | ❌ |
| `/api/auth/login` | POST | 登录 | ❌ |
| `/api/research` | POST | 同步研究 | ✅ |
| `/api/research/stream` | POST | SSE 流式研究 | ✅ |
| `/api/sessions` | GET | 当前用户会话列表 | 可选 |
| `/api/sessions/{id}` | GET | 单个会话详情 | ✅ |
| `/api/health` | GET | 健康检查 | ❌ |
| `:8000/kb/upload` | POST | 上传知识库文件 | 带 user_id |
| `:8000/kb/files` | GET | KB 文件列表 | 带 user_id |
| `:8000/kb/files/{id}` | DELETE | 删除 KB 文件 | 带 user_id |
