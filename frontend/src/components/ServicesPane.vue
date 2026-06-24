<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'

interface ServiceStatus {
  id: string
  label: string
  description: string
  enabled: boolean
  available: boolean
  socket: string | null
}

const props = defineProps<{ sandboxId: string; active: boolean; running: boolean }>()

const services = ref<ServiceStatus[]>([])
const dirty = ref(false)

async function load(id: string) {
  const r = await fetch(`/api/sandboxes/${id}/services`)
  const data = await r.json()
  services.value = data.services
  dirty.value = false
}

async function save() {
  const body: Record<string, { enabled: boolean }> = {}
  for (const svc of services.value) {
    body[svc.id] = { enabled: svc.enabled }
  }
  await fetch(`/api/sandboxes/${props.sandboxId}/services`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ services: body }),
  })
  dirty.value = props.running
}

async function toggle(svc: ServiceStatus) {
  const SSH_AGENT_SERVICES = ['ssh_agent', 'onepassword']
  if (!svc.enabled && SSH_AGENT_SERVICES.includes(svc.id)) {
    // disable the other SSH agent service
    for (const other of services.value) {
      if (other.id !== svc.id && SSH_AGENT_SERVICES.includes(other.id) && other.enabled) {
        other.enabled = false
      }
    }
  }
  svc.enabled = !svc.enabled
  await save()
}

onMounted(() => load(props.sandboxId))
watch(() => props.sandboxId, (id) => load(id))
watch(() => props.active, (a) => { if (a) load(props.sandboxId) })
</script>

<template>
  <div class="svc-pane">
    <div v-if="dirty" class="restart-banner">Changes saved — restart sandbox to apply</div>

    <div class="svc-list">
      <div v-for="svc in services" :key="svc.id" class="svc-row">
        <button
          class="toggle-btn"
          :class="{ on: svc.enabled }"
          @click="toggle(svc)"
          :title="svc.enabled ? 'Disable' : 'Enable'"
        >{{ svc.enabled ? 'on' : 'off' }}</button>
        <div class="svc-info">
          <div class="svc-header">
            <span class="svc-label">{{ svc.label }}</span>
            <span v-if="svc.available && svc.socket" class="svc-socket">{{ svc.socket }}</span>
            <span v-else-if="!svc.available" class="svc-unavailable">not available</span>
          </div>
          <div class="svc-desc">{{ svc.description }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.svc-pane {
  padding: 16px;
  overflow-y: auto;
  height: 100%;
  box-sizing: border-box;
}

.restart-banner {
  background: #2a1a00;
  border: 1px solid #6b4c00;
  border-radius: 4px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 0.85rem;
  color: #fbbf24;
}

.svc-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.svc-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #1e1e1e;
}
.svc-row:last-child { border-bottom: none; }

.toggle-btn {
  flex-shrink: 0;
  margin-top: 2px;
  min-width: 36px;
  padding: 2px 6px;
  font-size: 0.72rem;
  font-family: monospace;
  font-weight: 600;
  border-radius: 3px;
  border: 1px solid #444;
  cursor: pointer;
  background: #1e1e1e;
  color: #555;
}
.toggle-btn.on {
  background: rgba(34,197,94,0.12);
  border-color: rgba(34,197,94,0.4);
  color: #4ade80;
}
.toggle-btn:hover { opacity: 0.8; }

.svc-info {
  flex: 1;
  min-width: 0;
}

.svc-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.svc-label {
  font-size: 0.88rem;
  font-weight: 600;
  color: #e0e0e0;
}

.svc-socket {
  font-family: monospace;
  font-size: 0.78rem;
  color: #4ade80;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 400px;
}

.svc-unavailable {
  font-size: 0.78rem;
  color: #555;
}

.svc-desc {
  font-size: 0.78rem;
  color: #666;
  margin-top: 2px;
}
</style>
