<script setup lang="ts">
import { ref, watch } from 'vue'
import TerminalPane from './TerminalPane.vue'
import DomainsPane from './DomainsPane.vue'

interface Sandbox {
  id: string
  profile: string
  cwd: string
  cmd: string
  started_at: number
  ended_at: number | null
  exit_code: number | null
}

const props = defineProps<{ sandboxId: string; sandbox: Sandbox | null }>()
const tab = ref<'terminal' | 'domains'>('terminal')

watch(() => props.sandboxId, () => { tab.value = 'terminal' })

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
      <span style="margin-left:auto;padding:10px 16px;font-size:0.8rem;color:#555;cursor:default" v-if="sandbox" :title="sandbox.cwd">
        {{ sandbox.profile }} · {{ shortCwd(sandbox.cwd) }} · {{ statusLabel(sandbox) }}
      </span>
    </div>

    <TerminalPane v-show="tab === 'terminal'" :sandbox-id="sandboxId" :active="tab === 'terminal'" />
    <DomainsPane v-show="tab === 'domains'" :sandbox-id="sandboxId" :active="tab === 'domains'" />
  </div>
</template>
