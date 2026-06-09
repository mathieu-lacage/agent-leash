<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'

interface Bind {
  host: string
  dest: string
  mode: 'ro' | 'rw'
}

interface FilesystemData {
  system: Bind[]
  profile: Bind[]
  user: Bind[]
  running?: boolean
}

const props = defineProps<{ sandboxId: string; active: boolean; running: boolean }>()

const data = ref<FilesystemData>({ system: [], profile: [], user: [] })
const dirty = ref(false)

const newHost = ref('')
const newDest = ref('')
const newMode = ref<'ro' | 'rw'>('ro')

async function load(id: string) {
  const r = await fetch(`/api/sandboxes/${id}/filesystem`)
  data.value = await r.json()
  dirty.value = false
}

async function save() {
  await fetch(`/api/sandboxes/${props.sandboxId}/filesystem`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ binds: data.value.user }),
  })
  dirty.value = props.running
}

async function removeUser(i: number) {
  data.value.user.splice(i, 1)
  await save()
}

async function toggleMode(b: Bind) {
  b.mode = b.mode === 'ro' ? 'rw' : 'ro'
  await save()
}

async function addBind() {
  if (!newHost.value.trim()) return
  data.value.user.push({
    host: newHost.value.trim(),
    dest: newDest.value.trim() || newHost.value.trim(),
    mode: newMode.value,
  })
  newHost.value = ''
  newDest.value = ''
  newMode.value = 'ro'
  await save()
}

onMounted(() => load(props.sandboxId))
watch(() => props.sandboxId, (id) => load(id))
watch(() => props.active, (a) => { if (a) load(props.sandboxId) })
</script>

<template>
  <div class="fs-pane">
    <div v-if="dirty" class="restart-banner">Changes saved — restart sandbox to apply</div>

    <table class="fs-table">
      <tbody>
        <tr class="section-row"><td colspan="4"><span class="section-label">System</span></td></tr>
        <tr v-for="b in data.system" :key="b.host">
          <td class="path">{{ b.host }}</td>
          <td class="path dest">{{ b.dest !== b.host ? '→ ' + b.dest : '' }}</td>
          <td class="mode-cell"><span :class="['badge', b.mode]">{{ b.mode }}</span></td>
          <td></td>
        </tr>

        <template v-if="data.profile.length > 0">
          <tr class="section-row"><td colspan="4"><span class="section-label">Profile</span></td></tr>
          <tr v-for="b in data.profile" :key="b.host">
            <td class="path">{{ b.host }}</td>
            <td class="path dest">{{ b.dest !== b.host ? '→ ' + b.dest : '' }}</td>
            <td class="mode-cell"><span :class="['badge', b.mode]">{{ b.mode }}</span></td>
            <td></td>
          </tr>
        </template>

        <tr class="section-row"><td colspan="4"><span class="section-label">User</span></td></tr>
        <tr v-for="(b, i) in data.user" :key="i">
          <td class="path">{{ b.host }}</td>
          <td class="path dest">{{ b.dest !== b.host ? '→ ' + b.dest : '' }}</td>
          <td class="mode-cell">
            <button class="badge mode-toggle" :class="b.mode" @click="toggleMode(b)">{{ b.mode }}</button>
          </td>
          <td><button class="del-btn" @click="removeUser(i)">✕</button></td>
        </tr>
        <tr class="add-row">
          <td><input v-model="newHost" placeholder="host path" class="path-input" @keyup.enter="addBind" /></td>
          <td><input v-model="newDest" :placeholder="newHost || 'dest (= host)'" class="path-input" @keyup.enter="addBind" /></td>
          <td class="mode-cell">
            <select v-model="newMode" class="mode-select">
              <option value="ro">ro</option>
              <option value="rw">rw</option>
            </select>
          </td>
          <td><button class="add-btn" @click="addBind">Add</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.fs-pane {
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

.fs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.fs-table td {
  padding: 3px 8px;
  vertical-align: middle;
}

.fs-table td:first-child { padding-left: 0; }

.section-row td { padding-top: 14px; padding-bottom: 4px; }
.section-row:first-child td { padding-top: 0; }

.section-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #555;
  font-weight: 600;
}

.path {
  font-family: monospace;
  color: #e0e0e0;
  white-space: nowrap;
}

.dest {
  font-family: monospace;
  color: #777;
  white-space: nowrap;
}

.mode-cell {
  width: 44px;
  white-space: nowrap;
}

.badge {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 3px;
  font-size: 0.75rem;
  font-weight: 600;
  font-family: monospace;
  border: none;
  cursor: default;
}

.badge.ro { background: rgba(59,130,246,0.15); color: #7dd3fc; }
.badge.rw { background: rgba(249,115,22,0.15);  color: #fdba74; }

.mode-toggle { cursor: pointer; }
.mode-toggle:hover { opacity: 0.75; }

.del-btn {
  background: none;
  border: none;
  color: #444;
  cursor: pointer;
  font-size: 0.75rem;
  padding: 2px 6px;
}
.del-btn:hover { color: #f87171; }

.add-row td { padding-top: 10px; }

.path-input {
  font-family: monospace;
  font-size: 0.82rem;
  border: 1px solid #333;
  border-radius: 3px;
  padding: 3px 6px;
  width: 100%;
  box-sizing: border-box;
  background: #1a1a1a;
  color: #e0e0e0;
}
.path-input:focus { outline: none; border-color: #555; background: #222; }
.path-input::placeholder { color: #555; }

.mode-select {
  font-family: monospace;
  font-size: 0.82rem;
  border: 1px solid #333;
  border-radius: 3px;
  padding: 3px 4px;
  background: #1a1a1a;
  color: #e0e0e0;
  width: 44px;
}

.add-btn {
  background: #2a2a2a;
  color: #ccc;
  border: 1px solid #444;
  border-radius: 3px;
  padding: 4px 12px;
  font-size: 0.82rem;
  cursor: pointer;
  white-space: nowrap;
}
.add-btn:hover { background: #383838; color: #fff; }
</style>
