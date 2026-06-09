<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import TerminalPane from './TerminalPane.vue'
import DomainsPane from './DomainsPane.vue'
import FilesystemPane from './FilesystemPane.vue'
import ServicesPane from './ServicesPane.vue'

interface Sandbox {
  id: string
  profile: string
  cwd: string
  cmd: string
  started_at: number
  ended_at: number | null
  exit_code: number | null
}

const props = defineProps<{ sandboxId: string }>()
const tab = ref<'terminal' | 'domains' | 'filesystem' | 'services'>('terminal')
const sandboxMeta = ref<Sandbox | null>(null)
const isRunning = computed(() => sandboxMeta.value != null && sandboxMeta.value.ended_at == null)

watch(() => props.sandboxId, async (id) => {
  tab.value = 'terminal'
  const r = await fetch(`/api/sandboxes/${id}`)
  sandboxMeta.value = await r.json()
}, { immediate: true })

function statusLabel(s: Sandbox) {
  if (!s.ended_at) return 'running'
  return s.exit_code === 0 ? `exited (0)` : `exited (${s.exit_code})`
}

function shortCwd(cwd: string) {
  return cwd.replace(/\/$/, '').split('/').pop() || cwd
}
</script>

<template>
  <div class="tab-panel" style="display:flex;flex-direction:column;height:100%">
    <div class="tabs">
      <button class="tab-btn" :class="{ active: tab === 'terminal' }" @click="tab = 'terminal'">Terminal</button>
      <button class="tab-btn" :class="{ active: tab === 'domains' }" @click="tab = 'domains'">Domains</button>
      <button class="tab-btn" :class="{ active: tab === 'filesystem' }" @click="tab = 'filesystem'">Filesystem</button>
      <button class="tab-btn" :class="{ active: tab === 'services' }" @click="tab = 'services'">Services</button>
      <span v-if="sandboxMeta" style="margin-left:auto;padding:10px 16px;font-size:0.8rem;color:#555;cursor:default" :title="sandboxMeta.cwd">
        {{ sandboxMeta.profile }} · {{ shortCwd(sandboxMeta.cwd) }} · {{ statusLabel(sandboxMeta) }}
      </span>
    </div>

    <TerminalPane v-show="tab === 'terminal'" :sandbox-id="sandboxId" :active="tab === 'terminal'" />
    <DomainsPane v-show="tab === 'domains'" :sandbox-id="sandboxId" :active="tab === 'domains'" />
    <FilesystemPane v-show="tab === 'filesystem'" :sandbox-id="sandboxId" :active="tab === 'filesystem'" :running="isRunning" />
    <ServicesPane v-show="tab === 'services'" :sandbox-id="sandboxId" :active="tab === 'services'" :running="isRunning" />
  </div>
</template>
