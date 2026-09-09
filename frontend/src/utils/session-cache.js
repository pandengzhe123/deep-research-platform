/** 清空本地登录痕迹与会话缓存。
 *
 * 抽成独立函数：`auth.logout()`（用户主动退出）与 axios 401 拦截器（token 过期）
 * 必须做同一套清理 —— 只删 token 而留下 `chat_*` / `activeSession`，
 * 换账号登录后会看到上一个账号的提问和报告（信息泄露）。
 */
export function clearSessionCache() {
  localStorage.removeItem('token')
  localStorage.removeItem('username')
  localStorage.removeItem('role')
  localStorage.removeItem('activeSession')
  Object.keys(localStorage)
    .filter(k => k.startsWith('chat_'))
    .forEach(k => localStorage.removeItem(k))
}
