import { randomUUID } from 'node:crypto'

export const installationId = 'installation.ai4s.snapshot-launcher'
export const actionName = 'snapshot.read'

function delivery({ id, createdAt }, readIntervalSeconds) {
  const base = {
    schemaVersion: 1,
    revision: 1,
    title: '读取 AI4S 统计',
    enabled: true,
    action: {
      installationId, name: actionName, connections: {},
      input: { previous: { kind: 'pointer', source: 'placement_data', placementId: id, pointer: '/main' } },
    },
    createdAt,
    updatedAt: createdAt,
  }
  const automations = [
    { ...base, id: `automation.ai4s.refresh.${id}`, trigger: { kind: 'widget_intent', placementId: id, name: 'refresh' } },
    { ...base, id: `automation.ai4s.interval.${id}`, trigger: { kind: 'interval', everyMs: readIntervalSeconds * 1000 } },
  ]
  const bindings = automations.map(({ id }) => ({
    schemaVersion: 1, id: `binding.${id}`, kind: 'automation_data',
    automationId: id, enabled: true, source: '', target: '/main',
  }))
  return { automations, bindings }
}

export function bindSnapshot(board, widget, readIntervalSeconds, reportError) {
  let authoring
  let configuring

  function findPlacement(state) {
    return state.placements.find(({ id }) => state.packages[id]?.manifest.id === widget.id)
  }

  function configure() {
    const owner = authoring
    if (!owner || configuring) return
    const placement = findPlacement(board.state())
    if (!placement) return
    const { automations, bindings } = delivery(placement, readIntervalSeconds)
    if (bindings.every(({ id }) => placement.bindings.some((binding) => binding.id === id))) return

    configuring = (async () => {
      for (const automation of automations) {
        try {
          await owner.automationInspect({ automationId: automation.id })
        } catch (error) {
          if (error.code !== 'not_found') throw error
          await owner.automationApply({
            operationId: `ai4s.install.${automation.id}`, expectedRevision: 0, automation,
          })
        }
      }
      const state = board.state()
      const current = findPlacement(state)
      if (owner !== authoring || current?.id !== placement.id) return
      const missing = bindings.filter(({ id }) => !current.bindings.some((binding) => binding.id === id))
      await owner.boardApply({
        operationId: `ai4s.bind.${current.instanceId}.${current.revisionVersion}`,
        boardId: board.loadBoard().id,
        expectedBoardRevision: state.revision,
        operations: [{ kind: 'set_bindings', placementId: current.id, bindings: [...current.bindings, ...missing] }],
      })
      await owner.executeWidgetIntent({
        operationId: `ai4s.refresh.${randomUUID()}`, placementId: current.id, name: 'refresh', payload: {},
      })
    })().catch(reportError).finally(() => { configuring = undefined })
  }

  const stopBoard = board.onChange(configure)
  const stopAuthoring = board.onAuthoringChange((owner) => { authoring = owner; configure() })
  return () => {
    authoring = undefined
    stopAuthoring()
    stopBoard()
  }
}
