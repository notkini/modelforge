import {SVGProps} from 'react';
type Props=SVGProps<SVGSVGElement>&{size?:number};
const paths:Record<string,React.ReactNode>={
 ChevronDown:<path d="m6 9 6 6 6-6"/>,ChevronLeft:<path d="m15 18-6-6 6-6"/>,ChevronRight:<path d="m9 18 6-6-6-6"/>,
 CircleHelp:<><circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></>,ShieldCheck:<><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"/><path d="m9 12 2 2 4-4"/></>,
 Download:<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/></>,
 FileImage:<><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-4-4L5 21"/></>,
 Layers3:<><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></>,
 Menu:<><path d="M4 6h16M4 12h16M4 18h16"/></>,Moon:<path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z"/>,
 Plus:<path d="M12 5v14m-7-7h14"/>,Send:<><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></>,
 Settings2:<><path d="M20 7h-9m-4 0H4m16 10h-5m-4 0H4"/><circle cx="14" cy="7" r="3"/><circle cx="8" cy="17" r="3"/></>,
 Sparkles:<><path d="m12 3 1.9 5.8L20 11l-6.1 2.2L12 19l-2-5.8L4 11l6-2.2L12 3Z"/><path d="m19 14 1 2.5 2 1-2 1-1 2.5-1-2.5-2-1 2-1 1-2.5Z"/></>,
 Sun:<><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2m10-10h-2M4 12H2m17.1-7.1-1.4 1.4M6.3 17.7l-1.4 1.4m14.2 0-1.4-1.4M6.3 6.3 4.9 4.9"/></>,
 Trash2:<><path d="M3 6h18m-2 0-.9 14H5.9L5 6m4 0V4h6v2m-5 4v6m4-6v6"/></>,Archive:<><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v12h14V8m-9 4h4"/></>,X:<path d="m18 6-12 12M6 6l12 12"/>
 ,Pencil:<><path d="m16 4 4 4M4 20l4-.8L19.2 8a2.1 2.1 0 0 0-3-3L5 16.2 4 20Z"/></>
};
function make(name:string){return function Icon({size=18,...props}:Props){return <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>}}
export const Archive=make('Archive'),ChevronDown=make('ChevronDown'),ChevronLeft=make('ChevronLeft'),ChevronRight=make('ChevronRight'),CircleHelp=make('CircleHelp'),Download=make('Download'),FileImage=make('FileImage'),Layers3=make('Layers3'),Menu=make('Menu'),Moon=make('Moon'),Pencil=make('Pencil'),Plus=make('Plus'),Send=make('Send'),Settings2=make('Settings2'),ShieldCheck=make('ShieldCheck'),Sparkles=make('Sparkles'),Sun=make('Sun'),Trash2=make('Trash2'),X=make('X');
