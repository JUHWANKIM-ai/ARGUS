import network from "../../public/data/network.json";
import companies from "../../public/data/companies.json";
import meta from "../../public/data/meta.json";
import HubGraph from "../../components/HubGraph";
import NetworkExplorer from "../../components/NetworkExplorer";
import ReverseLookup from "../../components/ReverseLookup";
import Limits from "../../components/Limits";
import { nameKey, shortName, slugIndex } from "../../lib/format";
import type { CompanySummary, Network } from "../../lib/types";

export const metadata = {
  title: "관계망 — ARGUS",
  description: "공시에 이름이 적힌 계약 상대를 모아 발주처 쪽에서도 보고 수주 쪽에서도 봅니다.",
};

/**
 * 🔴 **이걸 보러 온 사람이 하려는 일**: *"이 발주처가 누구와 일하는지, 이 회사가
 * 누구에게서 일을 받는지 본다."*
 */
export default function NetworkPage() {
  const { hubs, pairs } = network as Network;
  const list = companies as CompanySummary[];
  const slugByName = slugIndex(list);

  // 역방향의 첫 화면은 **가장 많은 곳에서 일을 받는 회사**로 연다 — 빈 화면으로 열지 않는다.
  const appearances = new Map<string, number>();
  for (const h of hubs) for (const p of h.partners) appearances.set(nameKey(p), (appearances.get(nameKey(p)) ?? 0) + 1);
  const initial =
    [...list].sort((a, b) => (appearances.get(nameKey(b.name)) ?? 0) - (appearances.get(nameKey(a.name)) ?? 0))[0];

  const multi = hubs.filter((h) => h.partners.length >= 2).length;

  return (
    <main className="page">
      <div className="container">
        <div className="head">
          <h1>관계망</h1>
          <p>
            계약 공시 {meta.counts.contracts.toLocaleString()}건에 이름이 적힌 계약 상대{" "}
            {meta.counts.partners}곳입니다. 그중 {multi}곳은 <strong>둘 이상의 회사</strong>와 이어져 있습니다 —
            발주처 하나가 여러 회사에 일을 주는 모양입니다.
          </p>
        </div>

        <section className="section" style={{ marginTop: 0 }}>
          <div className="section-head">
            <h2>여러 회사에 일을 주는 곳</h2>
            <span className="muted">원 크기 = 계약 건수</span>
          </div>
          <HubGraph hubs={hubs} slugByName={slugByName} minCount={2} minPartners={2} limit={25} />
        </section>

        <section className="section">
          <div className="section-head">
            <h2>거꾸로 보기</h2>
            <span className="muted">이 회사는 누구에게서 일을 받나</span>
          </div>
          <ReverseLookup hubs={hubs} companies={list} initialSlug={initial.slug} />
        </section>

        <section className="section">
          <div className="section-head">
            <h2>계약 상대 전부</h2>
            <span className="muted">{meta.counts.partners}곳 · 계약 건수순</span>
          </div>
          <NetworkExplorer hubs={hubs} companies={list} />
        </section>

        <section className="section">
          <div className="section-head">
            <h2>같은 곳에서 일을 받는 회사끼리</h2>
            <span className="muted">{pairs.length}쌍 · 어디를 함께 상대했는지 적었습니다</span>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>회사</th>
                  <th>회사</th>
                  <th>함께 상대한 곳</th>
                </tr>
              </thead>
              <tbody>
                {pairs.map((pair) => {
                  const sa = slugByName[nameKey(pair.a)];
                  const sb = slugByName[nameKey(pair.b)];
                  return (
                    <tr key={`${pair.a}|${pair.b}`}>
                      <td>
                        {sa ? (
                          <a className="link" href={`/company/${sa}/`}>
                            {shortName(pair.a)}
                          </a>
                        ) : (
                          shortName(pair.a)
                        )}
                      </td>
                      <td>
                        {sb ? (
                          <a className="link" href={`/company/${sb}/`}>
                            {shortName(pair.b)}
                          </a>
                        ) : (
                          shortName(pair.b)
                        )}
                      </td>
                      <td>{pair.via.map(shortName).join(" · ")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <Limits kinds={["network", "counterparty", "universe"]} />
      </div>
    </main>
  );
}
