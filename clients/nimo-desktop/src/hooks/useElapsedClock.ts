import { useEffect, useState } from "react";

/**
 * 共享秒级时钟：页面内所有运行卡/计时标签共用单一 setInterval，
 * 避免每个卡片各自创建 1s 定时器（多卡 = 多定时器）。
 *
 * 仅在任意卡片需要计时时启动；全部结束或组件卸载时自动停止。
 * 返回单调递增的 tick 计数，调用方以 tick 为依赖重算已运行时长。
 */
export function useElapsedClock(active: boolean): number {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!active) {
      setTick(0);
      return;
    }
    const interval = window.setInterval(() => {
      setTick((current) => current + 1);
    }, 1_000);
    return () => window.clearInterval(interval);
  }, [active]);

  return tick;
}

/** Format seconds as H:MM:SS / MM:SS (mirrors PySide6 _tick_elapsed). */
export function formatElapsedClock(startMs: number, tick: number): string {
  const secs = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
  // tick 仅作为重新计算触发器；实际时长始终由真实时间戳推导，
  // 避免定时器漂移累积误差。
  void tick;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
