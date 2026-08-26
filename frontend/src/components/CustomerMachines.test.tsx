import React from 'react';
import {Tab,Tabs} from '@mui/material';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import CustomerMachines,{CustomerMachinesTab} from './CustomerMachines';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 errorDetail:(_e:any,fallback:string)=>fallback,
}));

// Çalışma saati ve model yılı API'den sayı da string de gelebilir; ekran
// ikisini de kaldırmalı.
const machines=[
 {id:9,brand:'John Deere',model:'6120M',serial_number:'JD-1',chassis_number:'CH-7',
  status:'active',working_hours:'1450',model_year:2021,is_active:true},
 {id:10,brand:'Case',model:'JX90',serial_number:null,status:'maintenance',
  working_hours:320,is_active:false},
];

beforeEach(()=>{navigate.mockReset();get.mockReset();get.mockResolvedValue({data:machines})});
afterEach(cleanup);

const mount=()=>render(<ThemeProvider theme={createTheme()}>
 <MemoryRouter><CustomerMachines customerId={4}/></MemoryRouter>
</ThemeProvider>);

it('müşterinin makinelerini kendi ucundan çeker',async()=>{
 mount();
 await screen.findByText('John Deere 6120M');
 // Cari kartın yükünü büyütmemek için ayrı ve kiracı kapsamlı uç kullanılır;
 // pasif makineler de görünmeli, aksi halde park eksik görünür.
 expect(get).toHaveBeenCalledWith('/machines',{params:{customer_id:4,active:'all',limit:100}});
 expect(screen.getByText(/Seri JD-1 · Şasi CH-7 · 2021/)).toBeInTheDocument();
 expect(screen.getByText('1.450 saat')).toBeInTheDocument();
});

it('makine kartı tek tıkla makine detayını açar',async()=>{
 mount();
 const card=(await screen.findByText('John Deere 6120M')).closest('[role="button"]');
 fireEvent.click(card as Element);
 expect(navigate).toHaveBeenCalledWith('/makineler/9');
});

it('makinesi olmayan müşteride açıklayıcı boş durum gösterir',async()=>{
 get.mockResolvedValue({data:[]});
 mount();
 await screen.findByText('Bu müşteriye tanımlı makine yok.');
});

// MUI <Tabs> çocuklarına onChange/value/selected proplarını enjekte eder.
// Sarmalayıcı bunları Tab'e geçirmezse sekme görünür ama TIKLANMAZ — ekranda
// her şey yerinde durduğu için fark edilmesi zor bir kusur.
it('sekme sarmalayıcısı Tabs proplarını geçirir, yoksa sekme tıklanmaz',()=>{
 const onChange=vi.fn();
 // Seçili olmayan bir sekmeye tıklanmalı: MUI zaten seçili sekmede onChange
 // tetiklemez, o yüzden tek sekmeli kurgu bu kusuru yakalayamaz.
 render(<ThemeProvider theme={createTheme()}>
  <Tabs value={0} onChange={onChange}>
   <Tab label="Genel"/>
   <CustomerMachinesTab allowed/>
  </Tabs>
 </ThemeProvider>);
 fireEvent.click(screen.getByRole('tab',{name:'Makineler'}));
 expect(onChange).toHaveBeenCalled();
 expect(onChange.mock.calls[0][1]).toBe(1);
});

it('tedarikçide sekme hiç basılmaz',()=>{
 const {container}=render(<ThemeProvider theme={createTheme()}>
  <Tabs value={0}><CustomerMachinesTab allowed={false}/></Tabs>
 </ThemeProvider>);
 expect(container.querySelector('[role="tab"]')).toBeNull();
});
