import {useEffect} from 'react';
import Lenis from 'lenis';
import gsap from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';

import {prefersReducedMotion} from './animation';

export function usePremiumScroll(){
  useEffect(()=>{
    if(prefersReducedMotion()) return;
    const lenis=new Lenis({autoRaf:false,duration:1.05,smoothWheel:true,anchors:{offset:0}});
    const update=(time:number)=>lenis.raf(time*1000);
    lenis.on('scroll',ScrollTrigger.update); gsap.ticker.add(update);
    return ()=>{ gsap.ticker.remove(update); lenis.destroy(); };
  },[]);
}
