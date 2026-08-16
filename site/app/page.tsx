import meta from "../public/data/meta.json";
import companies from "../public/data/companies.json";
import network from "../public/data/network.json";
import CompanySearch from "../components/CompanySearch";
import HubGraph from "../components/HubGraph";
import Limits from "../components/Limits";
import { slugIndex } from "../lib/format";
import type { CompanySummary, Network } from "../lib/types";
import Link from "next/link";

/**
 * 랜딩.
 *
 * 🔴 **이걸 보러 온 사람이 하려는 일**: *"이게 뭘 해 주는 서비스인지 3초 안에 알고,
 * 내가 궁금한 회사로 들어간다."*
 *
 * 그래서 한 화면에 셋만 있다 — **관계망**(무엇을) · **검색**(어떻게) · **정정 훅**(왜).
 * 우리가 무엇을 만들었는지는 여기에 한 글자도 쓰지 않는다.
 */
export default function Home() {
  const { hubs } = network as Network;
  const list = companies as CompanySummary[];
  const slugByName = slugIndex(list);

  // 🔴 수는 전부 meta.json 에서 읽는다. 하드코딩 금지.
  const share = Math.round((meta.counts.corrections / meta.counts.contracts) * 10);
  const shown = hubs.filter((h) => h.count >= 5 && h.partners.length >= 2).length;

  return (
    <main className="page">
      <div className="container">
        <h1 style={{ fontSize: "var(--text-3xl)", maxWidth: "18ch" }}>누가 누구에게서 일을 받는가</h1>
        <p className="muted" style={{ fontSize: "var(--text-lg)", margin: "var(--gap-4) 0 var(--gap-5)" }}>
          공시 하나가 아니라 공시들 <strong style={{ color: "var(--ink)" }}>사이의 관계</strong>를 읽습니다.
        </p>

        <CompanySearch companies={list} examples={["삼성전자", "대우건설", "한화오션", "두산에너빌리티"]} />

        {/* 🔴 정정 훅 — 이 수가 사람을 붙잡는다 */}
        <div className="alert" style={{ marginTop: "var(--gap-6)" }}>
          <span>
            <span className="tag-corrected">정정</span> 계약 공시 10건 중 {share}건이 나중에 정정됩니다.
            DART 에서 원공시만 보면 알 수 없습니다.
          </span>
          <Link href="/corrections/">정정된 공시 보기 →</Link>
        </div>

        <section className="section">
          <div className="section-head">
            <h2>발주처 한 곳이 여러 회사에 일을 준다</h2>
            <span className="muted">
              계약 {meta.counts.contracts.toLocaleString()}건에서 뽑은 거래상대 {meta.counts.partners}곳 중
              큰 {shown}곳 · <Link className="link" href="/network/">전부 보기 →</Link>
            </span>
          </div>
          <HubGraph hubs={hubs} slugByName={slugByName} minCount={5} minPartners={2} limit={10} />
        </section>

        <section className="section">
          <div className="grid">
            <Link className="tile" href="/company/">
              <span className="tile-name">이 회사, 최근에 뭘 했나</span>
              <span className="tile-meta">
                <span>
                  {meta.ranges.companies}개사의 계약·정정·거래상대를 한 화면에 모았습니다. 공시를 하나씩 열지 않습니다.
                </span>
              </span>
            </Link>
            <Link className="tile" href="/corrections/">
              <span className="tile-name">이 공시, 나중에 바뀌었나</span>
              <span className="tile-meta">
                <span>
                  정정 {meta.counts.corrections}건을 원공시와 이어 붙이고 금액이 얼마에서 얼마로 바뀌었는지 보여 줍니다.
                </span>
              </span>
            </Link>
            <Link className="tile" href="/network/">
              <span className="tile-name">이 회사는 누구와 거래하나</span>
              <span className="tile-meta">
                <span>공시에 이름이 적힌 거래상대 {meta.counts.partners}곳을 모아 발주처 쪽에서도 보고 수주 쪽에서도 봅니다.</span>
              </span>
            </Link>
          </div>
        </section>

        {/* 🔴 첫 화면에서부터 범위를 말한다. 그림이 그럴듯할수록 경계를 먼저 밝혀야 한다. */}
        <Limits kinds={["universe", "network"]} />
      </div>
    </main>
  );
}
