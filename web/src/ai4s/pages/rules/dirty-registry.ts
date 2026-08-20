/**
 * per-panel dirty 注册表（issue #69 P2-C）。
 * Ai4sRulesPage 同层可挂多个上报方（如 L2 = 商密词表面板 + PII 新增/编辑对话框），
 * 各自经 onDirtyChange 上报；全页单 boolean 会被后到上报者覆盖
 * （词表弄脏后开关一次 PII 对话框，其挂载 effect 上报 false 即把词表 dirty 冲掉），
 * 改为按上报方 key 记账，互不复位。
 */
export interface DirtyRegistry {
  /** 取某个上报方的 dirty 回调；同 key 返回同一函数引用（面板 useEffect 的依赖保持稳定） */
  reporter: (key: string) => (dirty: boolean) => void;
  /** 任一上报方处于 dirty */
  any: () => boolean;
  /** 全部复位（确认丢弃后切层时调用；面板卸载也会自行上报 false 复位） */
  clear: () => void;
}

export function createDirtyRegistry(): DirtyRegistry {
  const dirtyKeys = new Set<string>();
  const reporters = new Map<string, (dirty: boolean) => void>();
  return {
    reporter(key) {
      let fn = reporters.get(key);
      if (!fn) {
        fn = (dirty) => {
          if (dirty) {
            dirtyKeys.add(key);
          } else {
            dirtyKeys.delete(key);
          }
        };
        reporters.set(key, fn);
      }
      return fn;
    },
    any: () => dirtyKeys.size > 0,
    clear: () => dirtyKeys.clear(),
  };
}
