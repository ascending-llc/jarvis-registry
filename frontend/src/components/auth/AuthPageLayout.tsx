import type React from 'react';
import logo from '@/assets/jarvis_logo_w_text_light_bkg.svg';

export const Footer: React.FC = () => (
  <footer className='py-4 text-center text-sm text-[var(--jarvis-muted)]'>
    © {new Date().getFullYear()} Jarvis. All rights reserved.
  </footer>
);

export const AuthPageLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className='min-h-screen bg-[var(--jarvis-card)] flex flex-col'>
    <header className='bg-[var(--jarvis-card)] border-b border-[color:var(--jarvis-border)] py-4 text-center'>
      <img src={logo} alt='Jarvis Registry Logo' className='h-12 w-auto mx-auto' />
    </header>
    <main className='flex-grow flex items-center justify-center px-4 py-8'>{children}</main>
    <Footer />
  </div>
);
