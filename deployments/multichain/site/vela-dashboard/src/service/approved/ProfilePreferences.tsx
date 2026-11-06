import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { UserRound, Settings, Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/common/Panel';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { AccountTradingSettings } from './AccountPanels';

type Theme='night'|'day';
const read=(key:string)=>{try{return localStorage.getItem(key);}catch{return null;}};
const avatarValue=(value:string|null)=>value && value.length<700_000 && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value)?value:null;
type Preferences={theme:Theme;avatar:string|null;error:string;setTheme:(t:Theme)=>void;setAvatar:(file:File|null)=>Promise<void>};
const ProfileContext=createContext<Preferences>({theme:'night',avatar:null,error:'',setTheme:()=>{},setAvatar:async()=>{}});

/** Browser-local appearance only; these preferences never authorize trading. */
export function ProfileProvider({wallet,children,defaultTheme='night'}:{wallet:string;children:ReactNode;defaultTheme?:Theme}) {
  const avatarKey='vela:avatar:'+wallet.toLowerCase();
  const [theme,setThemeState]=useState<Theme>(()=>{const saved=read('vela:theme');return saved==='day'||saved==='night'?saved:defaultTheme;});
  const [avatar,setAvatarState]=useState<string|null>(()=>avatarValue(read(avatarKey)));
  const [error,setError]=useState('');
  useEffect(()=>{document.documentElement.dataset.theme=theme;document.documentElement.classList.toggle('dark',theme==='night');},[theme]);
  const persist=(key:string,value:string|null)=>{try{if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,value);}catch{throw Error('Your browser could not save this setting.');}};
  const setTheme=(value:Theme)=>{setThemeState(value);setError('');try{persist('vela:theme',value);}catch(e){setError((e as Error).message);}};
  const setAvatar=async(file:File|null)=>{
    setError('');
    try {
      if(file===null){persist(avatarKey,null);setAvatarState(null);return;}
      if(!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size>500_000)throw Error('Choose a JPG, PNG or WebP picture under 500 KB.');
      const value=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(Error('Could not read this picture.'));reader.readAsDataURL(file);});
      if(!avatarValue(value))throw Error('Choose a valid JPG, PNG or WebP picture.');
      persist(avatarKey,value);setAvatarState(value);
    } catch(e){setError((e as Error).message);}
  };
  return <ProfileContext.Provider value={{theme,avatar,error,setTheme,setAvatar}}>{children}</ProfileContext.Provider>;
}

export function ProfilePicture({large=false}:{large?:boolean}) {
  const {avatar}=useContext(ProfileContext);
  return <span className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-primary/30 bg-primary/10 ${large?'h-20 w-20':'h-9 w-9'}`}>
    {avatar?<img src={avatar} alt="Profile picture" className="h-full w-full object-cover" />:<UserRound aria-hidden className={large?'h-9 w-9 text-primary':'h-4 w-4 text-primary'} />}
  </span>;
}

export function ProfileMenu() {
  return <DropdownMenu><DropdownMenuTrigger asChild><button type="button" aria-label="Open profile menu" className="rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"><ProfilePicture /></button></DropdownMenuTrigger><DropdownMenuContent align="end" className="w-48"><DropdownMenuLabel className="flex items-center gap-3"><ProfilePicture /><span>Profile</span></DropdownMenuLabel><DropdownMenuSeparator /><DropdownMenuItem asChild><Link to="/settings"><Settings className="mr-2 h-4 w-4" />Settings</Link></DropdownMenuItem></DropdownMenuContent></DropdownMenu>;
}

export function AccountSettings() {
  const {theme,error,setTheme,setAvatar}=useContext(ProfileContext);
  return <><h1 className="font-display text-xl font-semibold">Settings</h1><div className="grid gap-4 lg:grid-cols-2"><div className="space-y-4"><Panel title="Profile"><div className="flex flex-wrap items-center gap-4"><ProfilePicture large /><div><label className="inline-flex cursor-pointer rounded-md border border-input px-4 py-2 text-sm font-medium hover:bg-accent">Change picture<input type="file" accept="image/png,image/jpeg,image/webp" aria-label="Change profile picture" className="sr-only" onChange={e=>{const file=e.target.files?.[0];if(file)void setAvatar(file);e.target.value='';}} /></label><Button variant="ghost" onClick={()=>void setAvatar(null)}>Remove</Button></div></div><p className="mt-3 text-xs text-muted-foreground">Saved in this browser. JPG, PNG or WebP, up to 500 KB.</p></Panel><Panel title="Appearance"><div className="flex gap-3"><Button variant={theme==='day'?'default':'outline'} aria-pressed={theme==='day'} onClick={()=>setTheme('day')}><Sun className="mr-2 h-4 w-4" />Light</Button><Button variant={theme==='night'?'default':'outline'} aria-pressed={theme==='night'} onClick={()=>setTheme('night')}><Moon className="mr-2 h-4 w-4" />Dark</Button></div></Panel>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}</div><section id="trading-settings"><AccountTradingSettings /></section></div></>;
}
