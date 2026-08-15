import Link from "next/link";

/**
 * 네 갈래. 🔴 **"소개" 다** — "방법론"·"기술" 이 아니다.
 * 이 사이트를 보러 온 사람은 우리가 어떻게 만들었는지가 아니라
 * **자기가 무엇을 할 수 있는지**를 알고 싶어 한다.
 */
const NAV = [
  { href: "/company", label: "기업" },
  { href: "/corrections", label: "정정 추적" },
  { href: "/network", label: "관계망" },
  { href: "/about", label: "소개" },
];

export default function Header() {
  return (
    <header className="nav">
      <div className="container nav-inner">
        <Link href="/" className="brand">
          ARGUS
          <span className="brand-tag">공시들 사이의 관계를 읽는다</span>
        </Link>
        <nav className="nav-links">
          {NAV.map((item) => (
            <Link key={item.href} href={item.href}>
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
