import { Routes } from '@angular/router';
import { PageRoute } from './page-route';

export const routes: Routes = [
  { path: '', component: PageRoute, pathMatch: 'full' },
  { path: 'harita', component: PageRoute },
  { path: 'kaynaklar', component: PageRoute },
  { path: 'kalite', component: PageRoute },
  { path: 'ayarlar', component: PageRoute },
  { path: 'sohbet', redirectTo: 'harita', pathMatch: 'full' },
  { path: '**', redirectTo: '' },
];
