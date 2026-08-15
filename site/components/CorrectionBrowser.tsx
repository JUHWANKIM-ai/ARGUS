"use client";

import { useEffect, useMemo, useState } from "react";
import CorrectionChain from "./CorrectionChain";
import type { CompanySummary, Correction } from "../lib/types";
import { nameKey } from "../lib/format";

type Props = { corrections: Correction[]; companies: CompanySummary[] };

type Track = "changed" | "same" | "unresolved";

/** 정정 하나가 어느 갈래인지. 🔴 셋을 섞으면 목록이 실제보다 완전해 보인다. */
function track(fix: Correction): Track {
  if (!fix.resolved) return "unresolved";
  if (fix.amount_before != null && fix.amount_after != null && fix.amount_before !== fix.amount_after) return "changed";
  return "same";
}

const TRACK_LABEL: Record<Track, string> = {
  changed: "금액이 바뀐 것",
  same: "금액은 그대로",
  unresolved: "추적 실패",
};

/**
 * 🔴 목록의 정렬과 거르기. 631건을 통째로 그리면 브라우저가 버벅이므로
 * **처음에는 50건**만 그리고 사람이 눌러 늘린다.
 */
export default function CorrectionBrowser({ corrections, companies }: Props) {
  const [query, setQuery] = useState("");
  const [tracks, setTracks] = useState<Track[]>(["changed", "same", "unresolved"]);
  const [limit, setLimit] = useState(50);

  // 🔴 회사 화면에서 `?company=<slug>` 를 달고 넘어온다.
  //    useSearchParams 를 쓰지 않는 이유: 정적 export 에서 Suspense 경계를 요구한다.
  useEffect(() => {
    const slug = new URLSearchParams(window.location.search).get("company");
    if (!slug) return;
    const hit = companies.find((c) => c.slug === slug);
    if (hit) setQuery(hit.name);
  }, [companies]);

  const counts = useMemo(() => {
    const c: Record<Track, number> = { changed: 0, same: 0, unresolved: 0 };
    for (const fix of corrections) c[track(fix)] += 1;
    return c;
  }, [corrections]);

  const rows = useMemo(() => {
    const key = nameKey(query);
    const order: Record<Track, number> = { changed: 0, same: 1, unresolved: 2 };
    return corrections
      .filter((fix) => tracks.includes(track(fix)))
      .filter((fix) => !key || nameKey(fix.corp_name).includes(key) || (fix.title ?? "").toLowerCase().includes(query.trim().toLowerCase()))
      // 🔴 금액이 바뀐 것을 앞에 — 그것이 사람이 놀라는 지점이다. 그다음은 최신순.
      .sort((a, b) => order[track(a)] - order[track(b)] || b.date.localeCompare(a.date));
  }, [corrections, query, tracks]);

  function toggle(t: Track) {
    setTracks((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
    setLimit(50);
  }

  return (
    <>
      <div className="filters">
        <input
          type="search"
          value={query}
          placeholder="회사 이름이나 계약명으로 거르기"
          aria-label="정정 공시 거르기"
          onChange={(event) => {
            setQuery(event.target.value);
            setLimit(50);
          }}
        />
        {(["changed", "same", "unresolved"] as const).map((t) => (
          <button
            key={t}
            type="button"
            className="btn ghost"
            aria-pressed={tracks.includes(t)}
            onClick={() => toggle(t)}
          >
            {TRACK_LABEL[t]} {counts[t]}
          </button>
        ))}
      </div>

      <p className="muted" style={{ marginTop: 0 }}>
        {rows.length.toLocaleString()}건
        {query.trim() ? ` — "${query.trim()}" 으로 걸렀습니다` : ""}
      </p>

      {rows.length === 0 ? (
        <p className="muted">해당하는 정정 공시가 없습니다 — 70개사 안에서만 찾습니다.</p>
      ) : (
        <ul className="fix-list">
          {rows.slice(0, limit).map((fix) => (
            <CorrectionChain key={fix.rcept_no} correction={fix} showCompany />
          ))}
        </ul>
      )}

      {limit < rows.length ? (
        <p style={{ marginTop: "var(--gap-3)" }}>
          <button type="button" className="btn ghost" onClick={() => setLimit((n) => n + 100)}>
            나머지 {(rows.length - limit).toLocaleString()}건 중 100건 더 보기
          </button>
        </p>
      ) : null}
    </>
  );
}
