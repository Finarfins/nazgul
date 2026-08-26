import {Box} from '@mui/material';

type HarmanBrandMarkProps={
 size?:number;
};

export default function HarmanBrandMark({size=44}:HarmanBrandMarkProps){
 return <Box
  component="svg"
  viewBox="0 0 64 64"
  role="img"
  aria-label="Harman Zamanı logosu"
  sx={{display:'block',width:size,height:size,flex:'0 0 auto'}}
 >
  <defs>
   <linearGradient id="harman-mark-bg" x1="8" y1="5" x2="54" y2="60" gradientUnits="userSpaceOnUse">
    <stop stopColor="#0b3567"/>
    <stop offset="1" stopColor="#061a38"/>
   </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="16" fill="url(#harman-mark-bg)"/>
  <path d="M12 47c10-8 21-11 40-10M16 52c10-7 21-9 36-8M23 56c8-4 17-6 28-5" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" opacity=".95"/>
  <path d="M20 38h25l3-5h-5l-3-7H27l-4 7h-6v5h3Z" fill="#fff"/>
  <path d="M29 26v-5h10l4 5" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinejoin="round"/>
  <circle cx="27" cy="39" r="4.5" fill="#f5c400" stroke="#071e41" strokeWidth="2"/>
  <circle cx="43" cy="39" r="3.2" fill="#f5c400" stroke="#071e41" strokeWidth="2"/>
  <path d="M49 31h8M50 34h8M51 37h7" stroke="#f5c400" strokeWidth="2.1" strokeLinecap="round"/>
  <path d="M15 29c2-8 7-14 14-18M18 29l-5-3M20 24l-5-3M23 19l-4-3M26 15l-3-3M18 27l5-1M20 22l5-1M23 17l4-1M26 13l3-1" fill="none" stroke="#f5c400" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
 </Box>;
}
