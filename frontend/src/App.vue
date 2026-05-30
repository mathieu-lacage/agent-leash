<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import SandboxList from './components/SandboxList.vue'
import SandboxDetail from './components/SandboxDetail.vue'
import DomainApproval from './components/DomainApproval.vue'

interface Sandbox {
  id: string
  profile: string
  cwd: string
  cmd: string
  started_at: number
  ended_at: number | null
  exit_code: number | null
}

interface ApprovalRequest {
  approval_id: string
  sandbox_id: string
  domain: string
}

const sandboxes = ref<Sandbox[]>([])
const selectedId = ref<string | null>(null)
const pendingApprovals = ref<ApprovalRequest[]>([])

const sidebarMode = ref<'live' | 'history'>('live')
const historyItems = ref<Sandbox[]>([])

let listWs: WebSocket | null = null
let approvalWs: WebSocket | null = null
let searchTimer: ReturnType<typeof setTimeout> | null = null

async function fetchSandboxes() {
  const r = await fetch('/api/sandboxes?running_only=true')
  sandboxes.value = await r.json()
  if (!selectedId.value && sandboxes.value.length > 0) {
    selectedId.value = sandboxes.value[0].id
  }
}

async function fetchHistory(q: string) {
  const url = q.trim() ? `/api/search?q=${encodeURIComponent(q.trim())}` : '/api/search'
  const r = await fetch(url)
  historyItems.value = await r.json()
}

function onSearch(q: string) {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => fetchHistory(q), 300)
}

function onModeChange(mode: 'live' | 'history') {
  sidebarMode.value = mode
  if (mode === 'history') fetchHistory('')
}

function connectListWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  listWs = new WebSocket(`${proto}://${location.host}/ws/sandboxes`)
  listWs.onmessage = () => {
    fetchSandboxes()
    if (sidebarMode.value === 'history') fetchHistory('')
  }
  listWs.onclose = () => setTimeout(connectListWs, 2000)
}

function sendFocus(focused: boolean) {
  if (approvalWs?.readyState === WebSocket.OPEN)
    approvalWs.send(JSON.stringify({ type: 'focus', focused }))
}

const onWindowFocus = () => sendFocus(true)
const onWindowBlur  = () => sendFocus(false)

function connectApprovalWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  approvalWs = new WebSocket(`${proto}://${location.host}/ws/approvals`)
  approvalWs.onopen = () => sendFocus(document.hasFocus())
  approvalWs.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.type === 'approval_request') {
      pendingApprovals.value.push(msg)
    }
  }
  approvalWs.onclose = () => setTimeout(connectApprovalWs, 2000)
}

function onApprovalDecided(approvalId: string) {
  pendingApprovals.value = pendingApprovals.value.filter(a => a.approval_id !== approvalId)
}

onMounted(() => {
  fetchSandboxes()
  connectListWs()
  connectApprovalWs()
  window.addEventListener('focus', onWindowFocus)
  window.addEventListener('blur',  onWindowBlur)
})

onUnmounted(() => {
  listWs?.close()
  approvalWs?.close()
  window.removeEventListener('focus', onWindowFocus)
  window.removeEventListener('blur',  onWindowBlur)
})
</script>

<template>
  <div id="app">
    <aside class="sidebar">
      <SandboxList
        :sandboxes="sandboxes"
        :selected-id="selectedId"
        :mode="sidebarMode"
        :history-items="historyItems"
        @select="id => selectedId = id"
        @mode-change="onModeChange"
        @search="onSearch"
      />
    </aside>

    <main class="main">
      <SandboxDetail
        v-if="selectedId"
        :sandbox-id="selectedId"
        :sandbox="[...sandboxes, ...historyItems].find(s => s.id === selectedId) ?? null"
      />
      <div v-else class="empty">Select a sandbox</div>
    </main>

    <DomainApproval
      v-for="req in pendingApprovals"
      :key="req.approval_id"
      :request="req"
      @decided="onApprovalDecided"
    />
  </div>
</template>
