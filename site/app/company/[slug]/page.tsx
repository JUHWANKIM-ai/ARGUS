import { readFileSync } from "node:fs";
import path from "node:path";
import companies from "../../../public/data/companies.json";
import network from "../../../public/data/network.json";
import ContractTable from "../../../components/ContractTable";
import CorrectionChain from "../../../components/CorrectionChain";
import Limits from "../../../components/Limits";
import { KIND_LABEL, nameKey, shortName, slugIndex, won } from "../../../lib/format";
import type { CompanyDetail, CompanySummary, Correction, Network } from "../../../lib/types";

const list = companies as CompanySummary[];
const slugByName = slugIndex(list);

/** 🔴 빌드 타임에만 돈다. 정적 export 라 런타임 파일 읽기는 존재하지 않는다. */
function loadCompany(slug: string): CompanyDetail {
  const file = path.join(process.cwd(), "public", "data", "company", `${slug}.json`);
  return JSON.parse(readFileSync(file, "utf-8")) as CompanyDetail;
}

export function generateStaticParams() {
  return list.map((c) => ({ slug: c.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const c = list.find((x) => x.slug === slug);
  return {
    title: c ? `${c.name} — ARGUS` : "ARGUS",
    description: c
      ? `${c.name}의 계약 공시 ${c.n_disclosures}건과 나중에 정정된 공시 ${c.n_corrections}건을 한 화면에서 봅니다.`
      : undefined,
  };
}

/**
 * 🔴 **핵심 화면.** 이 한 장이 서비스의 전부라고 봐도 된다.
 *
 * 🔴 **이걸 보러 온 사람이 하려는 일**: *"이 회사가 최근 뭘 했는지, 그중 나중에 바뀐 게
 * 있는지 한 화면에서 안다."* — DART 에서 공시 수십 건을 하나씩 열어야 하는 일이다.
 */
export default async function CompanyPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const company = loadCompany(slug);
  const { hubs } = network as Network;

  const correctionByRcept: Record<string, Correction> = {};
  for (const fix of company.corrections) correctionByRcept[fix.rcept_no] = fix;

  // 🔴 금액이 바뀐 것을 앞에. 그것이 사람이 놀라는 지점이다.
  const fixes = [...company.corrections].sort((a, b) => {
    const ca = a.amount_before != null && a.amount_after != null && a.amount_before !== a.amount_after ? 0 : 1;
    const cb = b.amount_before != null && b.amount_after != null && b.amount_before !== b.amount_after ? 0 : 1;
    return ca - cb || b.date.localeCompare(a.date);
  });
  const FIX_SHOWN = 8;

  const partners = [...company.partners].sort((a, b) => (b.contracts ?? 0) - (a.contracts ?? 0));

  // 역방향 — 이 회사를 계약 상대로 적은 회사들. 🔴 관계망에서 우리 회사는
  // 파트너가 아니라 **허브**로 잡히기도 한다(삼성전자가 그렇다).
  const key = nameKey(company.name);
  const asHub = hubs.find((h) => nameKey(h.canonical) === key || nameKey(h.name) === key);

  return (
    <main className="page">
      <div className="container">
        <div className="head">
          <p className="eyebrow">{company.sector}</p>
          <h1 style={{ fontSize: "var(--text-3xl)" }}>{company.name}</h1>
        </div>

        <dl className="stats">
          <div className="stat">
            <dt>공시</dt>
            <dd>
              {company.n_disclosures.toLocaleString()}
              <small> 건</small>
            </dd>
          </div>
          <div className="stat">
            <dt>계약 공시</dt>
            <dd>
              {company.contracts.length.toLocaleString()}
              <small> 건</small>
            </dd>
          </div>
          <div className="stat">
            <dt>나중에 정정</dt>
            <dd style={company.n_corrections > 0 ? { color: "var(--tone-corrected)" } : undefined}>
              {company.n_corrections.toLocaleString()}
              <small> 건</small>
            </dd>
          </div>
          <div className="stat">
            <dt>계약 상대</dt>
            <dd>
              {partners.length.toLocaleString()}
              <small> 곳</small>
            </dd>
          </div>
        </dl>

        {/* 🔴 N=0 이면 이 블록을 아예 안 그린다. "정정 없음" 도 쓰지 않는다 — 잡음이다. */}
        {company.n_corrections > 0 ? (
          <div className="alert">
            <span>
              이 회사의 공시 <strong>{company.n_corrections}건</strong>이 나중에 정정됐습니다.
            </span>
            <a href="#corrections">무엇이 바뀌었나 보기 →</a>
          </div>
        ) : null}

        {/* 재무 요약 — 🔴 뽑히지 않았으면 이 블록을 안 그린다. 추정하지 않는다. */}
        {company.financials ? (
          <section className="section">
            <div className="section-head">
              <h2>재무 요약</h2>
              {/* 🔴 어느 문서의 어느 기간인지를 수와 함께 쓴다 — 분기 매출을 연간으로
                  읽으면 네 배 틀린다. 접수번호가 있으면 DART 에서 검산할 수 있다. */}
              <span className="muted">
                정기공시 「요약재무정보」에서 그대로 옮겼습니다 · 접수번호{" "}
                <span className="numeric">{company.financials.source_rcept_no}</span>
              </span>
            </div>
            <dl className="stats">
              <div className="stat">
                <dt>{company.financials.period} {company.financials.basis} 매출액</dt>
                <dd>{won(company.financials.revenue) ?? <span className="tag-unknown">확인되지 않음</span>}</dd>
              </div>
              <div className="stat">
                <dt>{company.financials.period} {company.financials.basis} 영업이익</dt>
                <dd>
                  {won(company.financials.operating_income) ?? (
                    <span className="tag-unknown">확인되지 않음</span>
                  )}
                </dd>
              </div>
            </dl>
          </section>
        ) : null}

        {company.n_corrections > 0 ? (
          <section className="section" id="corrections">
            <div className="section-head">
              <h2>나중에 정정된 공시</h2>
              <span className="muted">
                금액이 바뀐 것부터 ·{" "}
                <a className="link" href={`/corrections/?company=${company.slug}`}>
                  전부 보기 →
                </a>
              </span>
            </div>
            <ul className="fix-list">
              {fixes.slice(0, FIX_SHOWN).map((fix) => (
                <CorrectionChain key={fix.rcept_no} correction={fix} />
              ))}
            </ul>
            {fixes.length > FIX_SHOWN ? (
              <p className="muted" style={{ marginTop: "var(--gap-3)" }}>
                {fixes.length - FIX_SHOWN}건이 더 있습니다 —{" "}
                <a className="link" href={`/corrections/?company=${company.slug}`}>
                  정정 추적에서 전부 보기 →
                </a>
              </p>
            ) : null}
          </section>
        ) : null}

        <section className="section">
          <div className="section-head">
            <h2>계약 공시</h2>
            <span className="muted">공시일 최신순 · 정정된 공시는 표시했습니다</span>
          </div>
          <ContractTable
            contracts={company.contracts}
            correctionByRcept={correctionByRcept}
            slugByName={slugByName}
          />
        </section>

        {partners.length > 0 ? (
          <section className="section">
            <div className="section-head">
              <h2>계약 상대</h2>
              <span className="muted">이 회사가 공시에 적은 상대 · 건수순</span>
            </div>
            <div className="chips">
              {partners.map((p) => {
                const s = slugByName[nameKey(p.name)];
                const body = (
                  <>
                    <span className="dot" data-kind={p.kind} />
                    <span>{shortName(p.name)}</span>
                    <span className="count">
                      {p.contracts != null ? `${p.contracts}건` : "건수 미상"}
                    </span>
                  </>
                );
                return s ? (
                  <a className="chip" key={p.name} href={`/company/${s}/`}>
                    {body}
                  </a>
                ) : (
                  <span className="chip" key={p.name}>
                    {body}
                  </span>
                );
              })}
            </div>
          </section>
        ) : null}

        {asHub && asHub.partners.length > 0 ? (
          <section className="section">
            <div className="section-head">
              <h2>이 회사를 상대로 적은 회사</h2>
              <span className="muted">
                {asHub.count}건의 계약 공시가 {company.name}를 계약 상대로 적었습니다 ·{" "}
                {KIND_LABEL[asHub.kind] ?? asHub.kind}
              </span>
            </div>
            <div className="chips">
              {asHub.partners.map((name) => {
                const s = slugByName[nameKey(name)];
                return s ? (
                  <a className="chip" key={name} href={`/company/${s}/`}>
                    <span>{shortName(name)}</span>
                  </a>
                ) : (
                  <span className="chip" key={name}>
                    <span>{shortName(name)}</span>
                  </span>
                );
              })}
            </div>
          </section>
        ) : null}

        <Limits kinds={["universe", "counterparty", "correction"]} />
      </div>
    </main>
  );
}
