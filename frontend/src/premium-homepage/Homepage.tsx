import {useEffect,useRef} from 'react';

import {HomepageTimelineManager} from './animation';
import {Contact,Footer,Header,Hero,QuickActions,Services,Support} from './components';
import Seo from './Seo';
import './premium-homepage.css';

export default function Homepage(){
  const root=useRef<HTMLElement>(null);
  useEffect(()=>{if(!root.current)return;const manager=new HomepageTimelineManager();manager.mount(root.current);return()=>manager.destroy()},[]);
  return <main ref={root} className="premium-homepage"><Seo/><a className="ph-skip" href="#bicerdöverler">İçeriğe geç</a><Header/><Hero/><QuickActions/><Services/><Support/><Contact/><Footer/></main>;
}
