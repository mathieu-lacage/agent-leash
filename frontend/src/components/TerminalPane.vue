<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'

const props = defineProps<{ sandboxId: string; active: boolean }>()

const container = ref<HTMLDivElement | null>(null)
const browserMaster = ref(false)
const zoomMode = ref(true)
let term: Terminal | null = null
let fitAddon: FitAddon | null = null
let ws: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null
let tabInstanceId: string | null = null
let instanceMismatch = false
// natural (unscaled) pixel size of .xterm-screen, captured once after term.resize()
let naturalW = 0
let naturalH = 0

function captureNaturalSize() {
  // call only when transform is already '' (right after term.resize, before applyZoom)
  const screen = container.value?.querySelector('.xterm-screen') as HTMLElement | null
  if (!screen) return
  naturalW = screen.offsetWidth
  naturalH = screen.offsetHeight
}

function applyZoom() {
  if (!container.value || !naturalW || !naturalH) return
  const screen = container.value.querySelector('.xterm-screen') as HTMLElement | null
  if (!screen) return
  const pad = 16
  const scale = Math.min(
    (container.value.clientWidth  - pad) / naturalW,
    (container.value.clientHeight - pad) / naturalH,
  )
  screen.style.transform = `scale(${scale})`
  screen.style.transformOrigin = 'top left'
}

function clearZoom() {
  const screen = container.value?.querySelector('.xterm-screen') as HTMLElement | null
  if (screen) screen.style.transform = ''
}

function toggleZoom() {
  zoomMode.value = !zoomMode.value
  if (zoomMode.value) {
    captureNaturalSize()
    applyZoom()
  } else {
    clearZoom()
  }
}

function connect(id: string) {
  cleanup()
  browserMaster.value = false
  zoomMode.value = true
  naturalW = 0
  naturalH = 0

  term = new Terminal({
    theme: { background: '#000000', foreground: '#e0e0e0' },
    fontFamily: '"JetBrains Mono", "Fira Code", monospace',
    fontSize: 13,
    cursorBlink: true,
    scrollback: 10000,
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.loadAddon(new WebLinksAddon())
  term.open(container.value!)

  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws/terminal/${id}`)
  ws.binaryType = 'arraybuffer'

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)

    if (msg.type === 'init') {
      if (tabInstanceId === null) {
        tabInstanceId = msg.instance_id
      } else if (tabInstanceId !== msg.instance_id) {
        instanceMismatch = true
        ws?.close()
        return
      }
      browserMaster.value = msg.browser_master
      if (msg.browser_master) {
        fitAddon!.fit()
        resizeObserver = new ResizeObserver(() => fitAddon?.fit())
        if (container.value) resizeObserver.observe(container.value)
      } else {
        term!.resize(msg.cols, msg.rows)
        nextTick(() => { captureNaturalSize(); applyZoom() })
        resizeObserver = new ResizeObserver(() => { if (zoomMode.value) applyZoom() })
        if (container.value) resizeObserver.observe(container.value)
      }
      return
    }

    if (msg.type === 'resize') {
      if (!browserMaster.value) {
        term!.resize(msg.cols, msg.rows)
        nextTick(() => { captureNaturalSize(); if (zoomMode.value) applyZoom() })
      }
      return
    }

    if (msg.type === 'output') {
      const bytes = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0))
      term!.write(bytes)
    }
  }

  ws.onclose = () => {
    if (instanceMismatch) return
    setTimeout(() => {
      if (props.sandboxId === id) connect(id)
    }, 2000)
  }

  term.onData((data) => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data: btoa(data) }))
    }
  })

  term.onResize(({ cols, rows }) => {
    if (browserMaster.value && ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols, rows }))
    }
  })
}

function cleanup() {
  resizeObserver?.disconnect()
  resizeObserver = null
  ws?.close()
  ws = null
  term?.dispose()
  term = null
}

onMounted(() => connect(props.sandboxId))
onUnmounted(() => cleanup())

watch(() => props.sandboxId, (id) => connect(id))
watch(() => props.active, (active) => {
  if (active && browserMaster.value) setTimeout(() => fitAddon?.fit(), 50)
  if (active && !browserMaster.value && zoomMode.value) setTimeout(() => { captureNaturalSize(); applyZoom() }, 50)
})
</script>

<template>
  <div
    class="terminal-wrap"
    :class="{ 'terminal-fixed': !browserMaster && !zoomMode }"
    ref="container"
  >
    <button v-if="!browserMaster" class="terminal-display-toggle" @click="toggleZoom">
      {{ zoomMode ? '1:1' : 'Fit' }}
    </button>
  </div>
</template>
