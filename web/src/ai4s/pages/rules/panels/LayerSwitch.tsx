/**
 * 分层总开关（issue #40）：L1 格式规则 / L2 词表·PII / 响应侧三层面板头部共用的主开关。
 * 即改即存：读 settings → 合并 PUT（整体替换语义，其余段原样带上）；
 * 与「开关与阈值」面板读写同一份 settings.json，关闭即整层跳过（保存即热生效）。
 * settings 查询失败/缺失（404 env 兜底态）时不渲染开关（label 一并隐藏）——不臆造状态
 * （管线节点徽标已显示「未知」）。
 */
import { Switch } from '@/components/ui/switch';
import { usePutSettings, useSettings, type DlpSettings } from '../api';

export function Ai4sLayerSwitch({
  section,
  label = '总开关',
}: {
  section: 'l1' | 'l2' | 'response';
  label?: string;
}) {
  const { data } = useSettings();
  const putSettings = usePutSettings();
  if (!data) return null;
  const toggle = (c: boolean) => {
    // 旧 settings.json 可能缺 l1/l2/response 段（shim 侧缺段默认 true）：
    // 合并时先按缺省 true 补齐三段再覆盖目标段，避免 PUT 出缺段文档被服务端 400；
    // 目标段展开保留既有子键（issue #127：l2.opf 子节——重建 {enabled} 会整体丢键）
    const keep = (s: 'l1' | 'l2' | 'response') =>
      section === s ? { ...data[s], enabled: c } : (data[s] ?? { enabled: true });
    const doc: DlpSettings = { ...data, l1: keep('l1'), l2: keep('l2'), response: keep('response') };
    putSettings.mutate(doc);
  };
  return (
    <div className='flex items-center gap-3'>
      <span className='text-sm whitespace-nowrap text-muted-foreground'>{label}</span>
      <Switch
        checked={data[section]?.enabled ?? true}
        disabled={putSettings.isPending}
        onCheckedChange={toggle}
      />
    </div>
  );
}
