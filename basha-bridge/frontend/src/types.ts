export type BusEvent = {
  tag: string;
  ts?: number;
  [key: string]: unknown;
};

export type FixtureInfo = {
  name: string;
  exists: boolean;
  lang: string;
  speaker: string;
  text: string;
  notes: string;
  default_tgt: string;
};

export type LatencyRow = {
  seg: string;
  reason: string;
  src: string;
  tgt: string;
  translate_ms?: number;
  tts_ms?: number;
  ear_ms?: number;
};

export type SegmentSpoken = BusEvent & {
  tag: "segment.spoken";
  seg: string;
  tts_ms: number;
  ear_ms: number;
  audio_s: number;
  direction?: string;
  speaker?: string;
};

export type SegmentTranslated = BusEvent & {
  tag: "segment.translated";
  seg: string;
  src: string;
  tgt: string;
  reason: string;
  translate_ms: number;
  direction?: string;
  speaker?: string;
};
