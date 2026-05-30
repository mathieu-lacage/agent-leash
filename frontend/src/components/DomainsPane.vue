<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'

interface DomainDecision {
  domain: string
  allowed: number
  decided_at: number
}

const props = defineProps<{ sandboxId: string; active: boolean }>()
const domains = ref<DomainDecision[]>([])

async function load(id: string) {
  const r = await fetch(`/api/sandboxes/${id}`)
  const data = await r.json()
  domains.value = data.domains ?? []
}

async function toggle(d: DomainDecision) {
  await fetch(`/api/sandboxes/${props.sandboxId}/domains/${encodeURIComponent(d.domain)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ allowed: !d.allowed }),
  })
  await load(props.sandboxId)
}

onMounted(() => load(props.sandboxId))
watch(() => props.sandboxId, (id) => load(id))
watch(() => props.active, (a) => { if (a) load(props.sandboxId) })

function ts(ms: number) {
  return new Date(ms).toLocaleTimeString()
}
</script>

<template>
  <div class="domain-table">
    <table v-if="domains.length > 0">
      <thead>
        <tr>
          <th>Domain</th>
          <th>Decision</th>
          <th>Time</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="d in domains" :key="d.domain">
          <td>{{ d.domain }}</td>
          <td :class="d.allowed ? 'allowed-yes' : 'allowed-no'">
            {{ d.allowed ? 'allowed' : 'blocked' }}
          </td>
          <td style="color:#666;font-size:0.8rem">{{ ts(d.decided_at) }}</td>
          <td>
            <button
              class="domain-toggle"
              :class="d.allowed ? 'domain-toggle-block' : 'domain-toggle-allow'"
              @click="toggle(d)"
            >{{ d.allowed ? 'Block' : 'Allow' }}</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty">No domain decisions yet</div>
  </div>
</template>
