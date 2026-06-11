<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import SandboxDetail from './components/SandboxDetail.vue'
import DomainApproval from './components/DomainApproval.vue'

interface ApprovalRequest {
  approval_id: string
  sandbox_id: string
  domain: string
}

const currentSandboxId = ref<string | null>(null)
const pendingApprovals = ref<ApprovalRequest[]>([])
const domainRefreshKey = ref(0)
const showNotifBanner = ref('Notification' in window && Notification.permission === 'default')
const sessionEnded = ref(false)

let listWs: WebSocket | null = null
let approvalWs: WebSocket | null = null
let knownInstanceId: string | null = null

function notifyApproval(req: ApprovalRequest) {
  if (document.hasFocus() || Notification.permission !== 'granted') return
  const n = new Notification(`Allow ${req.domain}?`, {
    body: `Sandbox ${req.sandbox_id.slice(0, 8)} wants network access`,
    tag: `aleash-${req.domain}`,
  })
  n.onclick = () => { window.focus(); n.close() }
}


async function fetchCurrentSandbox() {
  const r = await fetch('/api/current-sandbox')
  const { id } = await r.json()
  currentSandboxId.value = id
}

function checkInstanceId(id: string): boolean {
  if (knownInstanceId === null) {
    knownInstanceId = id
    return true
  }
  if (knownInstanceId !== id) {
    sessionEnded.value = true
    return false
  }
  return true
}

function connectListWs() {
  if (sessionEnded.value) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  listWs = new WebSocket(`${proto}://${location.host}/ws/sandboxes`)
  listWs.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.type === 'hello') { checkInstanceId(msg.instance_id); return }
    fetchCurrentSandbox()
  }
  listWs.onclose = () => { if (!sessionEnded.value) setTimeout(connectListWs, 2000) }
}

function connectApprovalWs() {
  if (sessionEnded.value) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  approvalWs = new WebSocket(`${proto}://${location.host}/ws/approvals`)
  approvalWs.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.type === 'hello') { if (!checkInstanceId(msg.instance_id)) approvalWs?.close(); return }
    if (msg.type === 'approval_request') {
      pendingApprovals.value.push(msg)
      notifyApproval(msg)
    }
  }
  approvalWs.onclose = () => { if (!sessionEnded.value) setTimeout(connectApprovalWs, 2000) }
}

function onApprovalDecided(approvalId: string) {
  pendingApprovals.value = pendingApprovals.value.filter(a => a.approval_id !== approvalId)
  domainRefreshKey.value++
}

async function requestNotifPermission() {
  await Notification.requestPermission()
  showNotifBanner.value = false
}

onMounted(() => {
  fetchCurrentSandbox()
  connectListWs()
  connectApprovalWs()
})

onUnmounted(() => {
  listWs?.close()
  approvalWs?.close()
})
</script>

<template>
  <div id="app">
    <main class="main">
      <SandboxDetail v-if="currentSandboxId" :sandbox-id="currentSandboxId" :domain-refresh-key="domainRefreshKey" />
      <div v-else class="empty">No running sandbox</div>
    </main>

    <DomainApproval
      v-for="req in pendingApprovals"
      :key="req.approval_id"
      :request="req"
      @decided="onApprovalDecided"
    />

    <div v-if="sessionEnded" class="session-ended-banner">
      This tab belongs to a previous aleash session. Close it or open a new tab.
    </div>

    <div v-if="showNotifBanner" class="notif-banner">
      <span>Enable desktop notifications to be alerted when a sandbox requests network access.</span>
      <button class="notif-btn-enable" @click="requestNotifPermission">Enable notifications</button>
      <button class="notif-btn-dismiss" @click="showNotifBanner = false">Dismiss</button>
    </div>
  </div>
</template>

<style scoped>
.session-ended-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  background: #5a1a1a;
  color: #ffcccc;
  text-align: center;
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  z-index: 2000;
}

.notif-banner {
  position: fixed;
  bottom: 1rem;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: #2a2a2a;
  border: 1px solid #555;
  border-radius: 6px;
  padding: 0.6rem 1rem;
  font-size: 0.85rem;
  color: #ddd;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  z-index: 1000;
  white-space: nowrap;
}

.notif-btn-enable {
  background: #4a90d9;
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 0.3rem 0.7rem;
  cursor: pointer;
  font-size: 0.82rem;
}

.notif-btn-enable:hover {
  background: #5aa0e9;
}

.notif-btn-dismiss {
  background: transparent;
  color: #aaa;
  border: 1px solid #555;
  border-radius: 4px;
  padding: 0.3rem 0.7rem;
  cursor: pointer;
  font-size: 0.82rem;
}

.notif-btn-dismiss:hover {
  color: #ddd;
  border-color: #888;
}
</style>
