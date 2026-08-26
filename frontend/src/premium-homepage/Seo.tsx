import {useEffect} from 'react';

const upsert=(selector:string,attributes:Record<string,string>)=>{
  let node=document.head.querySelector<HTMLMetaElement>(selector);
  const created=!node;
  if(!node){node=document.createElement(selector.startsWith('link')?'link':'meta') as HTMLMetaElement;document.head.append(node);}
  const previous=Object.fromEntries(Object.keys(attributes).map(key=>[key,node?.getAttribute(key)]));
  Object.entries(attributes).forEach(([key,value])=>node?.setAttribute(key,value));
  return ()=>{if(created){node?.remove();return}Object.entries(previous).forEach(([key,value])=>value===null?node?.removeAttribute(key):node?.setAttribute(key,value))};
};

export default function Seo(){
  useEffect(()=>{
    const previousTitle=document.title;
    const title='Sungur Tarım | Biçerdöver, Yedek Parça ve Uzman Servis'; document.title=title;
    const description='New Holland biçerdöver, orijinal yedek parça ve uzman servis desteği. Çorlu ve çevresinde hasat döneminde yanınızdayız.';
    const cleanups=[
      upsert('meta[name="description"]',{name:'description',content:description}),
      upsert('meta[property="og:title"]',{property:'og:title',content:title}),
      upsert('meta[property="og:description"]',{property:'og:description',content:description}),
      upsert('meta[property="og:type"]',{property:'og:type',content:'website'}),
      upsert('meta[property="og:image"]',{property:'og:image',content:new URL('/media/sungur-hero.webp',location.origin).href}),
      upsert('meta[name="twitter:card"]',{name:'twitter:card',content:'summary_large_image'}),
      upsert('meta[name="twitter:title"]',{name:'twitter:title',content:title}),
      upsert('meta[name="twitter:description"]',{name:'twitter:description',content:description}),
      upsert('link[rel="canonical"]',{rel:'canonical',href:new URL('/tanitim',location.origin).href}),
    ];
    const script=document.createElement('script'); script.type='application/ld+json'; script.dataset.homepageSchema='';
    script.text=JSON.stringify({'@context':'https://schema.org','@graph':[{'@type':'Organization',name:'Sungur Tarım',url:location.origin,description},{'@type':'BreadcrumbList',itemListElement:[{'@type':'ListItem',position:1,name:'Ana Sayfa',item:new URL('/tanitim',location.origin).href}]}]});
    document.head.append(script); return ()=>{script.remove();cleanups.reverse().forEach(cleanup=>cleanup());document.title=previousTitle};
  },[]);
  return null;
}
