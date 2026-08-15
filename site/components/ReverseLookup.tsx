"use client";

import { useMemo, useState } from "react";
import type { CompanySummary, Hub } from "../lib/types";
import { KIND_LABEL, nameKey, shortName } from "../lib/format";

type Props = { hubs: Hub[]; companies: CompanySummary[]; initialSlug: string };

/**
 * 🔴 **역방향.** 관계망은 보통 "이 발주처가 누구에게 일을 줬나" 로 읽히는데,
 * 회사 쪽에서 궁금한 것은 반대다 — *"우리 회사는 누구에게서 일을 받나."*
 * 같은 데이터를 뒤집어 보여 준다.
 */
export default function ReverseLookup({ hubs, companies, initialSlug }: Props) {
  const [slug, setSlug] = useState(initialSlug);
  const picked = companies.find((c) => c.slug === slug) ?? companies[0];

  const { receivesFrom, ordersTo } = useMemo(() => {
    const key = nameKey(picked?.name ?? "");
    return {
      receivesFrom: hubs
        .filter((h) => h.partners.some((p) => nameKey(p) === key))
        .sort((a, b) => b.count - a.count),
      ordersTo: hubs.find((h) => nameKey(h.canonical) === key || nameKey(h.name) === key),
    };
  }, [hubs, picked]);

  return (
    <>
      <div className="filters">
        <label className="muted" htmlFor="reverse-company">
          회사
        </label>
        <select
          id="reverse-company"
          value={slug}
          onChange={(event) => setSlug(event.target.value)}
          style={{
            height: 34,
            padding: "0 var(--gap-3)",
            fontFamily: "inherit",
            fontSize: "var(--text-sm)",
            border: "1px solid var(--line-strong)",
            borderRadius: "var(--radius-sm)",
            background: "var(--surface-raised)",
            color: "var(--ink)",
          }}
        >
          {[...companies]
            .sort((a, b) => a.name.localeCompare(b.name, "ko"))
            .map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.name}
              </option>
            ))}
        </select>
        <a className="link" href={`/company/${picked?.slug}/`}>
          {picked?.name} 화면으로 →
        </a>
      </div>

      <p style={{ marginTop: 0 }}>
        <strong>{picked?.name}</strong>가 공시에 계약 상대로 적은 곳{" "}
        <strong>{receivesFrom.length}곳</strong>
        {ordersTo ? (
          <>
            {" · "}다른 회사가 {picked?.name}를 상대로 적은 계약 <strong>{ordersTo.count}건</strong>
          </>
        ) : null}
      </p>

      {receivesFrom.length > 0 ? (
        <div className="chips" style={{ marginTop: "var(--gap-3)" }}>
          {receivesFrom.map((h) => (
            <span className="chip" key={h.canonical}>
              <span className="dot" data-kind={h.kind} />
              <span>{shortName(h.name)}</span>
              <span className="count">
                {KIND_LABEL[h.kind] ?? h.kind} · {h.count}건
              </span>
            </span>
          ))}
        </div>
      ) : (
        <p className="muted">
          이 회사의 계약 공시에 상대가 적혀 있지 않거나, 상대가 다른 회사와 겹치지 않아 관계망에 잡히지 않았습니다.
        </p>
      )}

      {ordersTo && ordersTo.partners.length > 0 ? (
        <>
          <p style={{ marginTop: "var(--gap-5)" }}>
            <strong>{picked?.name}</strong>를 계약 상대로 적은 회사
          </p>
          <div className="chips" style={{ marginTop: "var(--gap-2)" }}>
            {ordersTo.partners.map((name) => {
              const hit = companies.find((c) => nameKey(c.name) === nameKey(name));
              return hit ? (
                <a className="chip" key={name} href={`/company/${hit.slug}/`}>
                  <span>{shortName(name)}</span>
                </a>
              ) : (
                <span className="chip" key={name}>
                  <span>{shortName(name)}</span>
                </span>
              );
            })}
          </div>
        </>
      ) : null}
    </>
  );
}
