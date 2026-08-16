"use client";

import { useMemo, useState } from "react";
import type { CompanySummary, Hub } from "../lib/types";
import { KIND_LABEL, KIND_ORDER, nameKey, shortName, slugIndex } from "../lib/format";
import Link from "next/link";

type Props = { hubs: Hub[]; companies: CompanySummary[] };

/**
 * 발주처 전량(145곳). 🔴 여기는 그림이 아니라 **목록**이다 —
 * 145곳 중 119곳이 수주사 한 곳뿐이라 그리면 선 119개가 나올 뿐이다.
 * 랜딩의 그림은 그중 방사형이 되는 것만 골라 그린다.
 */
export default function NetworkExplorer({ hubs, companies }: Props) {
  const [kinds, setKinds] = useState<string[]>([...KIND_ORDER]);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(30);
  const slugByName = useMemo(() => slugIndex(companies), [companies]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const h of hubs) c[h.kind] = (c[h.kind] ?? 0) + 1;
    return c;
  }, [hubs]);

  const rows = useMemo(() => {
    const key = nameKey(query);
    return hubs
      .filter((h) => kinds.includes(h.kind))
      .filter(
        (h) => !key || nameKey(h.name).includes(key) || h.partners.some((p) => nameKey(p).includes(key)),
      )
      .sort((a, b) => b.count - a.count || b.partners.length - a.partners.length);
  }, [hubs, kinds, query]);

  function toggle(kind: string) {
    setKinds((prev) => (prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind]));
    setLimit(30);
  }

  return (
    <>
      <div className="filters">
        <input
          type="search"
          value={query}
          placeholder="발주처나 수주사 이름으로 거르기"
          aria-label="관계망 거르기"
          onChange={(event) => {
            setQuery(event.target.value);
            setLimit(30);
          }}
        />
        {KIND_ORDER.map((kind) => (
          <button
            key={kind}
            type="button"
            className="btn ghost"
            aria-pressed={kinds.includes(kind)}
            onClick={() => toggle(kind)}
          >
            {KIND_LABEL[kind]} {counts[kind] ?? 0}
          </button>
        ))}
      </div>

      <p className="muted" style={{ marginTop: 0 }}>{rows.length.toLocaleString()}곳</p>

      {rows.length === 0 ? (
        <p className="muted">해당하는 곳이 없습니다.</p>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>발주처 · 계약 상대</th>
                <th>갈래</th>
                <th className="right">계약 공시</th>
                <th>이 곳을 상대로 적은 회사</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, limit).map((hub) => {
                const own = slugByName[nameKey(hub.canonical)] ?? slugByName[nameKey(hub.name)];
                return (
                  <tr key={hub.canonical}>
                    <td>
                      {own ? (
                        <Link className="link" href={`/company/${own}/`}>
                          {shortName(hub.name)}
                        </Link>
                      ) : (
                        shortName(hub.name)
                      )}
                    </td>
                    <td>
                      <span className="tag-kind" data-kind={hub.kind}>
                        {KIND_LABEL[hub.kind] ?? hub.kind}
                      </span>
                    </td>
                    <td className="num right">{hub.count}건</td>
                    <td>
                      <div className="chips">
                        {hub.partners.map((name) => {
                          const s = slugByName[nameKey(name)];
                          return s ? (
                            <Link className="chip" key={name} href={`/company/${s}/`}>
                              <span>{shortName(name)}</span>
                            </Link>
                          ) : (
                            <span className="chip" key={name}>
                              <span>{shortName(name)}</span>
                            </span>
                          );
                        })}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {limit < rows.length ? (
        <p style={{ marginTop: "var(--gap-3)" }}>
          <button type="button" className="btn ghost" onClick={() => setLimit((n) => n + 50)}>
            나머지 {rows.length - limit}곳 중 50곳 더 보기
          </button>
        </p>
      ) : null}
    </>
  );
}
