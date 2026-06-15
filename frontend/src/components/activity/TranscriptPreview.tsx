import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, ListChecks, MessageSquareText, Tags } from "lucide-react";

type TranscriptTurn = {
  speaker: string;
  text: string;
};

type Props = {
  transcript: string;
  topics?: string[];
  actionItems?: string[];
  defaultTurns?: number;
};

function cleanText(value: string): string {
  return value
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function parseTranscriptTurns(transcript: string): TranscriptTurn[] {
  const lines = cleanText(transcript)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const turns: TranscriptTurn[] = [];
  const speakerRegex = /^([^:]{1,80}):\s*(.+)$/;

  for (const line of lines) {
    const match = line.match(speakerRegex);
    if (!match) {
      if (turns.length > 0) {
        turns[turns.length - 1].text = `${turns[turns.length - 1].text} ${line}`.trim();
      } else {
        turns.push({ speaker: "Transcript", text: line });
      }
      continue;
    }

    const speaker = cleanText(match[1]);
    const text = cleanText(match[2]);
    if (!text) continue;

    const last = turns[turns.length - 1];
    if (last && last.speaker.toLowerCase() === speaker.toLowerCase()) {
      last.text = `${last.text} ${text}`.trim();
      continue;
    }

    turns.push({ speaker, text });
  }

  return turns;
}

export default function TranscriptPreview({
  transcript,
  topics = [],
  actionItems = [],
  defaultTurns = 2,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  const turns = useMemo(() => parseTranscriptTurns(transcript), [transcript]);
  const visibleTurns = expanded ? turns : turns.slice(0, defaultTurns);
  const hiddenCount = Math.max(turns.length - visibleTurns.length, 0);
  const safeTopics = topics.filter(Boolean).slice(0, 6);
  const safeActions = actionItems.filter(Boolean).slice(0, expanded ? 6 : 3);
  const previewText = visibleTurns
    .map((turn) => `${turn.speaker}: ${turn.text}`)
    .join(" ")
    .trim();

  if (!cleanText(transcript)) return null;

  return (
    <div className="mt-5 rounded-[22px] border border-[#dfe8f2] bg-[#fbfdff] px-6 py-6">
      <div className="flex items-center justify-between gap-3 flex-wrap border-b border-[#ebf1f7] pb-4">
        <div className="flex items-center gap-2">
          <MessageSquareText size={14} className="text-[#60758b]" />
          <span className="text-[13px] font-semibold text-[#48607b]">
            Transcript
          </span>
        </div>
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          className="inline-flex items-center gap-1 rounded-full border border-[#d7e2ee] bg-white px-3.5 py-1.5 text-[12px] font-semibold text-[#48607b]"
        >
          {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {expanded ? "Collapse" : hiddenCount > 0 ? "Show full transcript" : "Show transcript"}
        </button>
      </div>

      {safeActions.length > 0 && (
        <div className="mt-5 rounded-2xl border border-[#e4ebf3] bg-white px-5 py-5">
          <div className="mb-4 flex items-center gap-2 text-[12px] font-semibold text-[#5f7389]">
            <ListChecks size={13} />
            Action items
          </div>
          <div className="space-y-3">
            {safeActions.map((item) => (
              <div
                key={item}
                className="rounded-xl border border-[#edf2f7] bg-[#fbfdff] px-4 py-3.5 text-[13px] leading-7 text-[#41566e]"
              >
                {item}
              </div>
            ))}
          </div>
        </div>
      )}

      {!expanded && (
        <div className="mt-5 rounded-2xl border border-[#e4ebf3] bg-white px-5 py-5">
          <div className="mb-3 text-[12px] font-semibold text-[#5f7389]">
            Transcript preview
          </div>
          <div
            className="text-[14px] leading-[2.05] text-[#30455b]"
            style={{
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {previewText || cleanText(transcript)}
          </div>
          {hiddenCount > 0 && (
            <div className="mt-4 text-[12px] text-[#7a8ea4]">
              {hiddenCount} more transcript turn{hiddenCount === 1 ? "" : "s"} hidden
            </div>
          )}
        </div>
      )}

      {expanded && (
        <>
          {safeTopics.length > 0 && (
            <div className="mt-5 flex items-start gap-2.5">
              <Tags size={13} className="mt-0.5 text-[#7a8ea4]" />
              <div className="flex flex-wrap gap-2">
                {safeTopics.map((topic) => (
                  <span
                    key={topic}
                    className="rounded-full border border-[#d8e6f5] bg-[#eef5ff] px-2.5 py-1 text-[11px] font-semibold text-[#335e8d]"
                  >
                    {topic}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="mt-5 space-y-4">
            {visibleTurns.length > 0 ? (
              visibleTurns.map((turn, index) => (
                <div key={`${turn.speaker}-${index}`} className="rounded-2xl border border-[#e4ebf3] bg-white px-5 py-5">
                  <div className="mb-3 text-[12px] font-semibold text-[#5f7389]">
                    {turn.speaker}
                  </div>
                  <div className="text-[14px] leading-[2.05] text-[#30455b]">
                    {turn.text}
                  </div>
                </div>
              ))
            ) : (
              <div className="rounded-2xl border border-[#e4ebf3] bg-white px-5 py-5 text-[14px] leading-[2.05] text-[#30455b]">
                {cleanText(transcript)}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
