import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,beforeAll,describe,expect,it,vi} from 'vitest';

import Homepage from './Homepage';

vi.mock('lenis',()=>({default:class{on(){} raf(){} destroy(){}}}));
vi.mock('gsap',()=>({default:{registerPlugin:vi.fn(),context:()=>({revert:vi.fn()}),timeline:()=>({from(){return this}}),utils:{toArray:()=>[]},ticker:{add:vi.fn(),remove:vi.fn(),lagSmoothing:vi.fn()}},gsap:{}}));
vi.mock('gsap/ScrollTrigger',()=>({ScrollTrigger:{refresh:vi.fn(),update:vi.fn()}}));

beforeAll(()=>{Object.defineProperty(window,'matchMedia',{writable:true,value:vi.fn().mockReturnValue({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()})})});
afterEach(cleanup);

describe('PremiumHomepage',()=>{
  it('renders semantic sections and primary actions',()=>{
    render(<Homepage/>);
    expect(screen.getByRole('heading',{level:1})).toHaveTextContent('Hasat durmaz');
    expect(screen.getByRole('link',{name:/Parça Sor/i})).toHaveAttribute('href','#parca');
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
  });
  it('provides accessible image text and navigation landmarks',()=>{
    render(<Homepage/>);
    expect(screen.getByAltText(/tarlada çalışan tarım makinesi/i)).toBeInTheDocument();
    expect(screen.getByRole('navigation',{name:'Ana menü'})).toBeInTheDocument();
    expect(screen.getByRole('navigation',{name:'Alt menü'})).toBeInTheDocument();
    expect(screen.getByRole('link',{name:'İçeriğe geç'})).toHaveAttribute('href','#bicerdöverler');
  });
  it('restores route metadata when unmounted',()=>{
    document.title='ERP';
    const view=render(<Homepage/>);
    expect(document.title).toContain('Sungur Tarım');
    expect(document.head.querySelector('link[rel="canonical"]')).toBeInTheDocument();
    view.unmount();
    expect(document.title).toBe('ERP');
    expect(document.head.querySelector('link[rel="canonical"]')).not.toBeInTheDocument();
    expect(document.head.querySelector('[data-homepage-schema]')).not.toBeInTheDocument();
  });
});
