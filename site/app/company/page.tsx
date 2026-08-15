import companies from "../../public/data/companies.json";
import CompanySearch from "../../components/CompanySearch";
import Limits from "../../components/Limits";
import type { CompanySummary } from "../../lib/types";

export const metadata = {
  title: "기업 — ARGUS",
  description: "ARGUS 가 다루는 70개사. 회사를 고르면 계약·정정·거래상대를 한 화면에서 봅니다.",
};

/**
 * 기업 목록.
 *
 * 🔴 **이걸 보러 온 사람이 하려는 일**: *"내가 아는 회사가 여기 있는지 확인하고
 * 그 회사 화면으로 들어간다."*
 *
 * 그래서 이 화면의 일은 **범위를 보여 주는 것**이다. 70개사를 통째로 깔아 놓는 것이
 * "70개사만 다룹니다" 라는 문장보다 정직하다 — 사람이 직접 세어 볼 수 있다.
 */
export default function CompanyIndex() {
  const list = companies as CompanySummary[];

  const bySector = new Map<string, CompanySummary[]>();
  for (const c of [...list].sort((a, b) => b.n_disclosures - a.n_disclosures)) {
    const bucket = bySector.get(c.sector);
    if (bucket) bucket.push(c);
    else bySector.set(c.sector, [c]);
  }
  const sectors = [...bySector.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], "ko"));

  return (
    <main className="page">
      <div className="container">
        <div className="head">
          <h1>기업 {list.length}곳</h1>
          <p>
            회사를 고르면 계약 공시 · 나중에 정정된 공시 · 거래상대를 한 화면에서 봅니다. 공시를 하나씩 열지 않습니다.
          </p>
        </div>

        <CompanySearch companies={list} placeholder="회사 이름이나 업종으로 찾기" />

        {sectors.map(([sector, members]) => (
          <section className="section" key={sector}>
            <div className="section-head">
              <h2 style={{ fontSize: "var(--text-lg)" }}>{sector}</h2>
              <span className="muted">{members.length}곳</span>
            </div>
            <div className="grid">
              {members.map((c) => (
                <a className="tile" key={c.slug} href={`/company/${c.slug}/`}>
                  <span className="tile-name">{c.name}</span>
                  <span className="tile-meta">
                    <span>공시 {c.n_disclosures}건</span>
                    {c.n_corrections > 0 ? (
                      <span className="tag-corrected">정정 {c.n_corrections}건</span>
                    ) : null}
                  </span>
                </a>
              ))}
            </div>
          </section>
        ))}

        <Limits kinds={["universe"]} />
      </div>
    </main>
  );
}
