<script setup lang="ts">
interface Sandbox {
  id: string
  profile: string
  cwd: string
  started_at: number
  ended_at: number | null
  exit_code: number | null
}

const props = defineProps<{
  sandboxes: Sandbox[]
  selectedId: string | null
  mode: 'live' | 'history'
  historyItems: Sandbox[]
}>()
const emit = defineEmits<{
  select: [id: string]
  modeChange: [mode: 'live' | 'history']
  search: [q: string]
}>()

function statusClass(s: Sandbox) {
  if (!s.ended_at) return 'running'
  return s.exit_code === 0 ? 'exited' : 'error'
}

function shortId(id: string) { return id.slice(0, 8) }

function relTime(ms: number) {
  const diff = Math.floor((Date.now() - ms) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`
  return `${Math.floor(diff/3600)}h ago`
}

const displayList = () => props.mode === 'live' ? props.sandboxes : props.historyItems
</script>

<template>
  <div class="sidebar-header">
    <span>Sandbox</span>
    <button
      class="sidebar-mode-btn"
      :class="{ active: mode === 'history' }"
      @click="emit('modeChange', mode === 'live' ? 'history' : 'live')"
      title="History"
    >⏱</button>
  </div>

  <div v-if="mode === 'history'" class="history-search-wrap">
    <input
      class="history-search"
      placeholder="Search sessions…"
      @input="emit('search', ($event.target as HTMLInputElement).value)"
    />
  </div>

  <div class="sandbox-list">
    <div
      v-for="s in displayList()"
      :key="s.id"
      class="sandbox-item"
      :class="{ active: s.id === selectedId }"
      @click="emit('select', s.id)"
    >
      <div class="name">
        <span class="status-dot" :class="statusClass(s)"></span>
        {{ s.profile }} · {{ shortId(s.id) }}
      </div>
      <div class="meta">{{ s.cwd }} · {{ relTime(s.started_at) }}</div>
    </div>
    <div v-if="displayList().length === 0" class="empty" style="padding:16px;font-size:0.8rem;color:#555">
      {{ mode === 'live' ? 'No running sandboxes' : 'No sessions found' }}
    </div>
  </div>
</template>
