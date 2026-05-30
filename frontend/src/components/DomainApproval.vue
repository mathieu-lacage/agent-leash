<script setup lang="ts">
interface ApprovalRequest {
  approval_id: string
  sandbox_id: string
  domain: string
}

const props = defineProps<{ request: ApprovalRequest }>()
const emit = defineEmits<{ decided: [approvalId: string] }>()

async function decide(approved: boolean, permanent: boolean = true) {
  await fetch(`/api/approvals/${props.request.approval_id}/decide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved, permanent }),
  })
  emit('decided', props.request.approval_id)
}
</script>

<template>
  <div class="modal-backdrop">
    <div class="modal">
      <h2>Domain access request</h2>
      <div class="domain">{{ request.domain }}</div>
      <div class="sub">
        Sandbox {{ request.sandbox_id.slice(0, 8) }} wants to make a network request.<br>
        Allow or block this domain?
      </div>
      <div class="modal-actions">
        <button class="btn btn-block" @click="decide(false, true)">Always block</button>
        <button class="btn btn-block" style="background:#555" @click="decide(false, false)">Block once</button>
        <button class="btn btn-allow" @click="decide(true, false)">Allow once</button>
        <button class="btn btn-always" @click="decide(true, true)">Always allow</button>
      </div>
    </div>
  </div>
</template>
