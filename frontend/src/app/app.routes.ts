import { Routes } from '@angular/router';
import { AuthPage } from './auth-page';
import { signedIn, signedOut } from './auth.service';
import { CompanyPage } from './company-page';
import { JoinPage } from './join';
import { MeetingRoom } from './meeting-room';

export const routes: Routes = [
  { path: 'login', component: AuthPage, canActivate: [signedOut], title: 'Tensor · Sign in' },
  { path: 'register', component: AuthPage, canActivate: [signedOut], title: 'Tensor · Register your company' },
  { path: 'invite/:code', component: AuthPage, canActivate: [signedOut], title: 'Tensor · Join your team' },
  { path: '', component: JoinPage, canActivate: [signedIn], title: 'Tensor · Join' },
  { path: 'company', component: CompanyPage, canActivate: [signedIn], title: 'Tensor · Company' },
  { path: 'm/:id', component: MeetingRoom, canActivate: [signedIn], title: 'Tensor · Meeting' },
  { path: '**', redirectTo: '' },
];
