import type {ReactNode} from 'react';

export function Container({children,className=''}:{children:ReactNode;className?:string}){
  return <div className={`ph-container ${className}`}>{children}</div>;
}

export function Header(){
  return <header className="ph-header">
    <Container className="ph-header-inner">
      <a className="ph-brand" href="#baslangic" aria-label="Sungur Tarım ana sayfa"><i aria-hidden="true">S</i><span>SUNGUR<strong>TARIM</strong></span></a>
      <nav aria-label="Ana menü"><a href="#bicerdöverler">Biçerdöverler</a><a href="#parca">Yedek Parça</a><a href="#servis">Servis</a><a href="#iletisim">İletişim</a></nav>
      <a className="ph-phone" href="tel:+902826517140">☎ 0282 651 71 40</a>
      <a className="ph-button ph-button-primary ph-header-cta" href="#servis">Servis Talebi</a>
    </Container>
  </header>;
}

export function Hero(){
  return <section className="ph-hero" id="baslangic" aria-labelledby="hero-title">
    <div className="ph-hero-media" data-parallax data-photo-slot="combine-harvester"><img src="/media/sungur-hero.webp" width="1816" height="872" alt="Gün batımında tarlada çalışan tarım makinesi" fetchPriority="high"/></div>
    <div className="ph-hero-overlay"/>
    <Container className="ph-hero-content">
      <span className="ph-dealer">NEW HOLLAND YETKİLİ BAYİ &amp; SERVİS</span>
      <h1 id="hero-title">Hasat durmaz.<br/>Biz de durmayız.</h1>
      <p>New Holland biçerdöver, orijinal yedek parça ve uzman servis desteği.</p>
      <div className="ph-actions"><a className="ph-button ph-button-primary" href="#parca">Parça Sor →</a><a className="ph-button ph-button-ghost" href="#servis">Servis Talebi Oluştur</a></div>
      <div className="ph-trust"><span>✓ Yetkili Bayi &amp; Servis</span><span>● Hızlı Parça Desteği</span><span>◆ Hasat Dönemi Destek</span></div>
    </Container>
  </section>;
}

const quickActions=[
  ['⌕','Parça Bul','Parça kodu veya makine modeliyle arayın','Parça kodu veya model girin','#parca'],
  ['⚒','Servis Talebi','Arızayı bildirin, ekibimiz size ulaşsın.','Servis Talebi Oluştur','#servis'],
  ['▣','Satılık Makineler','Güncel sıfır ve ikinci el makineler.','Makineleri İncele','#bicerdöverler'],
];
export function QuickActions(){
  return <section className="ph-quick" aria-label="Hızlı işlemler"><Container><div className="ph-quick-grid">{quickActions.map(([icon,title,copy,action,href])=><article key={title}><i aria-hidden="true">{icon}</i><div><h2>{title}</h2><p>{copy}</p>{title==='Parça Bul'?<label><span className="ph-sr-only">Parça ara</span><input placeholder={action}/><button type="button" aria-label="Parça ara">⌕</button></label>:<a href={href}>{action} →</a>}</div></article>)}</div></Container></section>;
}

const services=[
  ['bicerdöverler','BİÇERDÖVERLER','Hasadın en güçlü zamanı.','Yüksek kapasite, güvenilir performans ve doğru makine seçimi için yanınızdayız.','Makineleri incele'],
  ['parca','ORİJİNAL YEDEK PARÇA','Doğru parça, uzun ömür.','Makinenizin performansını koruyan doğru parçaya hızlıca ulaşın.','Parça talebi oluştur'],
  ['servis','UZMAN SERVİS','Sahada hızlı çözüm.','Deneyimli ekibimizle bakım, arıza ve hasat dönemi desteği alın.','Servis hizmetlerini gör'],
];
export function Services(){
  return <section className="ph-section ph-services"><Container><header className="ph-section-header"><span>SAHADA GÜCÜNÜZ</span><h2>Sahada gücünüz,<br/>serviste güvenceniz.</h2><p>Satıştan servis sonrasına kadar makinenizin her döneminde tek çözüm ortağı.</p></header><div className="ph-service-grid">{services.map(([id,eyebrow,title,copy,action],index)=><article id={id} key={id} className="ph-service-card"><div className={`ph-service-visual ph-service-${index+1}`}><span aria-hidden="true">{index===0?'▰':index===1?'⚙':'⚒'}</span></div><div><small>{eyebrow}</small><h3>{title}</h3><p>{copy}</p><a href="#iletisim">{action} →</a></div></article>)}</div></Container></section>;
}

export function Support(){
  return <section className="ph-support"><Container><div><span>7/24 HASAT HATTI</span><h2>Hasat zamanında beklemek yok.</h2><p>Acil arıza ve parça ihtiyacınızda doğrudan ekibimize ulaşın.</p></div><a className="ph-button ph-button-primary" href="tel:+902826517140">☎ 0282 651 71 40</a></Container></section>;
}

export function Contact(){
  return <section className="ph-section ph-contact" id="iletisim"><Container><div><span>ÇORLU • TEKİRDAĞ</span><h2>Makineniz için doğru çözümü birlikte bulalım.</h2></div><div className="ph-contact-actions"><a className="ph-button ph-button-primary" href="tel:+902826517140">Hemen Ara</a><a className="ph-button ph-button-dark" href="mailto:info@sungurlartarim.com">E-posta Gönder</a></div></Container></section>;
}

export function Footer(){
  return <footer className="ph-footer"><Container><div className="ph-footer-main"><a className="ph-brand" href="#baslangic"><i aria-hidden="true">S</i><span>SUNGUR<strong>TARIM</strong></span></a><nav aria-label="Alt menü"><a href="#bicerdöverler">Biçerdöverler</a><a href="#parca">Yedek Parça</a><a href="#servis">Servis</a><a href="/giris">ERP Girişi</a></nav></div><div className="ph-footer-bottom"><span>© {new Date().getFullYear()} Sungur Tarım. Tüm hakları saklıdır.</span><span>Çorlu • Tekirdağ</span></div></Container></footer>;
}
