export type EmptyKind = "search" | "filter" | "none";
export type StatusTone = "queued" | "running" | "waiting" | "failed" | "done";

export interface EmptyQuery {
  query: string;
  kind: string;
  scene: string;
  tag: string;
  from: string;
  to: string;
  status: string;
  total: number;
}

export interface EmptyCopy {
  icon: "search" | "filter" | "mic";
  title: string;
  hint: string;
  action: string;
}

export interface ConfirmRequest {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
}

const GROUP_TONE: Record<string, StatusTone> = {
  pending: "queued",
  processing: "running",
  done: "done",
  failed: "failed",
  skipped: "waiting",
};

const LABEL_TONE: Record<string, StatusTone> = {
  排队中: "queued",
  转写中: "running",
  已转写: "done",
  失败: "failed",
  太短已跳过: "waiting",
  已总结: "done",
  总结没成功: "failed",
  仅转写: "waiting",
  等待总结: "waiting",
  还没整理: "queued",
};

export function recordingsEmptyKind(input: EmptyQuery): EmptyKind {
  if (input.query.trim()) return "search";
  if (input.total <= 0) return "none";
  return "filter";
}

export function recordingsEmptyCopy(kind: EmptyKind): EmptyCopy {
  if (kind === "search") {
    return {
      icon: "search",
      title: "没有匹配的录音",
      hint: "换一个关键词，或清空搜索后再看。",
      action: "清空搜索",
    };
  }
  if (kind === "filter") {
    return {
      icon: "filter",
      title: "这个筛选下没有录音",
      hint: "换一个状态、标签或时间范围。",
      action: "清除筛选",
    };
  }
  return {
    icon: "mic",
    title: "还没有录音",
    hint: "导入一条音频，或等后台监控发现新的语音备忘录。",
    action: "导入音频",
  };
}

export function statusTone(group?: string, label?: string): StatusTone {
  if (label && LABEL_TONE[label]) return LABEL_TONE[label];
  if (group && GROUP_TONE[group]) return GROUP_TONE[group];
  return "queued";
}

export function deleteRuleConfirm(wrong: string, right: string): ConfirmRequest {
  return {
    title: "删掉这条纠错",
    message: `删掉「${wrong} → ${right}」？`,
    confirmLabel: "删除",
    danger: true,
  };
}

export function importConfirm(): ConfirmRequest {
  return {
    title: "导入音频",
    message: "导入后会马上转写，峰值大约 9.6GB。文件只写入数据目录。继续？",
    confirmLabel: "继续",
  };
}

export function retryOneConfirm(): ConfirmRequest {
  return {
    title: "重试这条",
    message: "重试这条会加载 Qwen，峰值大约 9.6GB。继续？",
    confirmLabel: "继续",
  };
}

export function retryFailedConfirm(): ConfirmRequest {
  return {
    title: "重试失败项",
    message: "重试所有失败的录音。有录音时会加载 Qwen。继续？",
    confirmLabel: "继续",
  };
}

export function retranscribeConfirm(): ConfirmRequest {
  return {
    title: "重新转写",
    message: "开始重转会加载 Qwen，峰值大约 9.6GB，并占用和后台任务同一把锁。继续？",
    confirmLabel: "开始重转",
    danger: true,
  };
}

export function summarizeConfirm(): ConfirmRequest {
  return {
    title: "重新整理",
    message: "会用本机模型（约 19GB）重新整理，并占用和后台任务同一把锁。原始转写不变。继续？",
    confirmLabel: "继续",
  };
}

export function digestConfirm(day: string): ConfirmRequest {
  return {
    title: "生成当日摘要",
    message: `用本机模型汇总 ${day} 的录音，写进 Obsidian 的当日摘要。会占用和转写同一把锁。继续？`,
    confirmLabel: "继续",
  };
}

export function monitorDialog(message: string): ConfirmRequest {
  return {
    title: "修改后台任务",
    message,
    confirmLabel: "确定",
    danger: true,
  };
}

export async function runIfConfirmed(confirm: () => boolean | Promise<boolean>, send: () => Promise<void>): Promise<"cancelled" | "sent"> {
  if (!(await confirm())) return "cancelled";
  await send();
  return "sent";
}
