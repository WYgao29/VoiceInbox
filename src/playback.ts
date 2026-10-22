export function applyPlaybackRate(audio: { playbackRate: number; defaultPlaybackRate: number }, rate: number): void {
  if (!Number.isFinite(rate) || rate <= 0) return;
  audio.playbackRate = rate;
  audio.defaultPlaybackRate = rate;
}
