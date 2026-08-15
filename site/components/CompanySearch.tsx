"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import type { CompanySummary } from "../lib/types";
import { nameKey } from "../lib/format";

type Props = {
  companies: CompanySummary[];
  placeholder?: string;
  /** 히어로 아래에 놓일 때는 예시를 보여 준다. 목록 화면에서는 군더더기다. */
  examples?: string[];
};

/**
 * 🔴 **회사 하나로 들어가는 문**이다. 이 사이트에서 사람이 처음 하는 일이
 * "내가 아는 회사를 친다" 라서 검색이 랜딩의 한복판에 있다.
 *
 * 한계를 그 자리에서 말한다 — 못 찾으면 *"70개사 안에서만 찾습니다"* 라고 쓴다.
 * 검색이 조용히 비면 사람은 자기가 틀린 줄 안다.
 */
export default function CompanySearch({ companies, placeholder, examples }: Props) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const router = useRouter();

  const matches = useMemo(() => {
    const key = nameKey(query);
    if (!key) return [];
    return companies
      .filter((c) => nameKey(c.name).includes(key) || nameKey(c.sector).includes(key))
      .sort((a, b) => {
        // 이름이 그 글자로 시작하는 회사를 앞에 — "삼성" 을 치면 삼성전자가 먼저다.
        const sa = nameKey(a.name).startsWith(key) ? 0 : 1;
        const sb = nameKey(b.name).startsWith(key) ? 0 : 1;
        return sa - sb || b.n_disclosures - a.n_disclosures;
      })
      .slice(0, 8);
  }, [companies, query]);

  function go(index: number) {
    const target = matches[index];
    if (target) router.push(`/company/${target.slug}/`);
  }

  return (
    <div className="search">
      <form
        className="search-row"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          go(cursor); // 🔴 엔터로 첫 결과(또는 고른 결과)로 바로 간다
        }}
      >
        <input
          type="search"
          value={query}
          aria-label="기업 검색"
          autoComplete="off"
          placeholder={placeholder ?? "기업 검색 — 삼성전자 · 대우건설 · 한화오션 …"}
          onChange={(event) => {
            setQuery(event.target.value);
            setCursor(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setCursor((c) => Math.min(c + 1, matches.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setCursor((c) => Math.max(c - 1, 0));
            }
          }}
        />
        <button className="btn" type="submit">
          찾기
        </button>
      </form>

      {query.trim() ? (
        <div className="search-results">
          {matches.length > 0 ? (
            matches.map((c, i) => (
              <a
                key={c.slug}
                href={`/company/${c.slug}/`}
                className={i === cursor ? "is-active" : undefined}
                onMouseEnter={() => setCursor(i)}
              >
                <span>
                  {c.name} <span className="muted">· {c.sector}</span>
                </span>
                <small>
                  공시 {c.n_disclosures}건
                  {c.n_corrections > 0 ? ` · 정정 ${c.n_corrections}건` : ""}
                </small>
              </a>
            ))
          ) : (
            // 🔴 한계를 그 자리에서 말한다. 빈 결과를 빈 채로 두지 않는다.
            <p className="muted">찾는 회사가 없습니다 — 70개사 안에서만 찾습니다.</p>
          )}
        </div>
      ) : null}

      {examples && examples.length > 0 ? (
        <p className="search-hint">
          예:{" "}
          {examples.map((name, i) => (
            <span key={name}>
              {i > 0 ? " · " : ""}
              <button
                type="button"
                className="link"
                style={{ background: "none", border: 0, padding: 0, cursor: "pointer", font: "inherit" }}
                onClick={() => {
                  setQuery(name);
                  setCursor(0);
                }}
              >
                {name}
              </button>
            </span>
          ))}
        </p>
      ) : null}
    </div>
  );
}
