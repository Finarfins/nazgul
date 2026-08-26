import gsap from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

export const motionQuery='(prefers-reduced-motion: reduce)';
export const prefersReducedMotion=()=>window.matchMedia(motionQuery).matches;

export function splitText(element:HTMLElement){
  if(element.dataset.split==='true') return Array.from(element.querySelectorAll<HTMLElement>('[data-word]'));
  const words=element.textContent?.trim().split(/\s+/)??[];
  element.textContent='';
  words.forEach((word,index)=>{
    const wrapper=document.createElement('span'); wrapper.className='ph-word-wrap';
    const span=document.createElement('span'); span.dataset.word=''; span.className='ph-word'; span.textContent=word;
    wrapper.append(span); element.append(wrapper,document.createTextNode(index===words.length-1?'':' '));
  });
  element.dataset.split='true';
  return Array.from(element.querySelectorAll<HTMLElement>('[data-word]'));
}

export class HomepageTimelineManager {
  private context?:gsap.Context;
  mount(root:HTMLElement){
    if(prefersReducedMotion()) return;
    this.context=gsap.context(()=>{
      const title=root.querySelector<HTMLElement>('[data-hero-title]');
      const words=title?splitText(title):[];
      gsap.timeline({defaults:{ease:'power3.out'}})
        .from('[data-hero-eyebrow]',{opacity:0,y:18,duration:.7})
        .from(words,{yPercent:110,stagger:.055,duration:1.05},'-.35')
        .from('[data-hero-copy], [data-hero-actions]',{opacity:0,y:28,stagger:.12,duration:.8},'-.55');
      gsap.utils.toArray<HTMLElement>('[data-reveal]').forEach((section)=>{
        gsap.from(section.children,{opacity:0,y:40,stagger:.09,duration:.85,ease:'power2.out',scrollTrigger:{trigger:section,start:'top 82%',once:true}});
      });
      gsap.utils.toArray<HTMLElement>('[data-parallax]').forEach((element)=>{
        gsap.to(element,{yPercent:10,ease:'none',scrollTrigger:{trigger:element.parentElement,start:'top bottom',end:'bottom top',scrub:.6}});
      });
    },root);
  }
  refresh(){ ScrollTrigger.refresh(); }
  destroy(){ this.context?.revert(); this.context=undefined; }
}
