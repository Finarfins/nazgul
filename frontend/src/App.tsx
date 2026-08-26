import React, {lazy, Suspense} from 'react';
import {BrowserRouter, Navigate, Route, Routes, useLocation} from 'react-router-dom';
import {Box, CircularProgress} from '@mui/material';

import ErrorBoundary from './components/ErrorBoundary';
import {useAuth} from './AuthContext';
import {permissionForPath} from './navigation';
import './field.css';

const Dashboard=lazy(()=>import('./pages/Dashboard'));
const Entities=lazy(()=>import('./pages/Entities'));
const EntityDetail=lazy(()=>import('./pages/EntityDetail'));
const Products=lazy(()=>import('./pages/Products'));
const ProductDetail=lazy(()=>import('./pages/ProductDetail'));
const Machines=lazy(()=>import('./pages/Machines'));
const MachineDetail=lazy(()=>import('./pages/MachineDetail'));
const WorkOrders=lazy(()=>import('./pages/WorkOrders'));
const WorkOrderDetail=lazy(()=>import('./pages/WorkOrderDetail'));
const Invoices=lazy(()=>import('./pages/Invoices'));
const InvoiceDetail=lazy(()=>import('./pages/InvoiceDetail'));
const Transactions=lazy(()=>import('./pages/Transactions'));
const Pos=lazy(()=>import('./pages/Pos'));
const Payments=lazy(()=>import('./pages/Payments'));
const PaymentAllocations=lazy(()=>import('./pages/PaymentAllocations'));
const Receivables=lazy(()=>import('./pages/Receivables'));
const StockMovements=lazy(()=>import('./pages/StockMovements'));
const TransferDetail=lazy(()=>import('./pages/TransferDetail'));
const StockTransfer=lazy(()=>import('./pages/StockTransfer'));
const InventoryCounts=lazy(()=>import('./pages/InventoryCountsReport'));
const InventoryCountDetail=lazy(()=>import('./pages/InventoryCountDetail'));
const Reports=lazy(()=>import('./pages/Reports'));
const ReceivablesAging=lazy(()=>import('./pages/ReceivablesAging'));
const PurchaseComparison=lazy(()=>import('./pages/PurchaseComparison'));
const PurchaseDashboard=lazy(()=>import('./pages/PurchaseDashboard'));
const PartSupersessions=lazy(()=>import('./pages/PartSupersessions'));
const SeasonalStockPlan=lazy(()=>import('./pages/SeasonalStockPlan'));
const Login=lazy(()=>import('./pages/Login'));
const Register=lazy(()=>import('./pages/Register'));
const VerifyEmail=lazy(()=>import('./pages/VerifyEmail'));
const ChangePassword=lazy(()=>import('./pages/ChangePassword'));
const ForgotPassword=lazy(()=>import('./pages/ForgotPassword'));
const ResetPassword=lazy(()=>import('./pages/ResetPassword'));
const Users=lazy(()=>import('./pages/Users'));
const Audit=lazy(()=>import('./pages/Audit'));
const ActivityLog=lazy(()=>import('./pages/ActivityLog'));
const Backups=lazy(()=>import('./pages/Backups'));
const Notifications=lazy(()=>import('./pages/Notifications'));
const NotificationTemplates=lazy(()=>import('./pages/NotificationTemplates'));
const SupplierPrices=lazy(()=>import('./pages/SupplierPrices'));
const Companies=lazy(()=>import('./pages/Companies'));
const Warehouses=lazy(()=>import('./pages/Warehouses'));
const WarehouseDetail=lazy(()=>import('./pages/WarehouseDetail'));
const Insights=lazy(()=>import('./pages/Insights'));
const AbsorptionRate=lazy(()=>import('./pages/AbsorptionRate'));
const WorkflowDocuments=lazy(()=>import('./pages/WorkflowDocuments'));
const Finance=lazy(()=>import('./pages/Finance'));
const HarvestSeasonAdmin=lazy(()=>import('./pages/HarvestSeasonAdmin'));
const PremiumHomepage=lazy(()=>import('./premium-homepage/Homepage'));
const FieldWorkOrders=lazy(()=>import('./pages/FieldWorkOrders'));
const FarmDashboard=lazy(()=>import('./pages/FarmDashboard'));
const Farms=lazy(()=>import('./pages/Farms'));
const CropSeasons=lazy(()=>import('./pages/CropSeasons'));
const FieldActivities=lazy(()=>import('./pages/FieldActivities'));
const FieldTasks=lazy(()=>import('./pages/FieldTasks'));
const FieldHarvests=lazy(()=>import('./pages/FieldHarvests'));
const ParcelDetail=lazy(()=>import('./pages/ParcelDetail'));
const QuickActivity=lazy(()=>import('./pages/QuickActivity'));
const HerdDashboard=lazy(()=>import('./pages/HerdDashboard'));
const Animals=lazy(()=>import('./pages/Animals'));
const AnimalDetail=lazy(()=>import('./pages/AnimalDetail'));
const HerdHealth=lazy(()=>import('./pages/HerdHealth'));
const HerdBreeding=lazy(()=>import('./pages/HerdBreeding'));
const HerdYields=lazy(()=>import('./pages/HerdYields'));
const CostRates=lazy(()=>import('./pages/CostRates'));
const AppShell=lazy(()=>import('./components/AppShell'));

function Loading(){
  return <Box minHeight="45dvh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>;
}

function Protected(){
  const {user,loading,can}=useAuth();
  const {pathname}=useLocation();
  if(loading)return <Loading/>;
  if(!user)return <Navigate to="/giris" replace/>;
  if(user.must_change_password)return <Navigate to="/sifre-degistir" replace/>;
  // Rota izni tek kaynaktan (navigation.ts) okunur. Koruma tek noktada
  // uygulandığı için menü ile route izni ayrışamaz ve yeni eklenen bir rota
  // sessizce korumasız kalamaz.
  if(!can(permissionForPath(pathname)))return <Navigate to="/" replace/>;
  return <AppShell/>;
}

export default function App(){
  return <ErrorBoundary><BrowserRouter><Suspense fallback={<Loading/>}><Routes>
    <Route path="tanitim" element={<PremiumHomepage/>}/>
    <Route path="giris" element={<Login/>}/>
    <Route path="kayit" element={<Register/>}/>
    <Route path="eposta-dogrula" element={<VerifyEmail/>}/>
    <Route path="sifre-degistir" element={<ChangePassword/>}/>
    <Route path="sifremi-unuttum" element={<ForgotPassword/>}/>
    {/* Sunucunun ürettiği sıfırlama bağlantısı bu yola gelir
        (backend/app/password_reset.py). Yolu değiştirirsen orayı da değiştir,
        yoksa postalanan linkler 404 döner. */}
    <Route path="sifre-sifirla" element={<ResetPassword/>}/>
    <Route element={<Protected/>}>
      <Route index element={<Dashboard/>}/>
      <Route path="satislar" element={<Transactions kind="sale"/>}/>
      <Route path="hizli-satis" element={<Pos/>}/>
      <Route path="alislar" element={<Transactions kind="purchase"/>}/>
      <Route path="belge-akislari" element={<WorkflowDocuments/>}/>
      <Route path="musteriler" element={<Entities type="customer"/>}/>
      <Route path="tedarikciler" element={<Entities type="supplier"/>}/>
      <Route path="musteriler/:id" element={<EntityDetail type="customer"/>}/>
      <Route path="tedarikciler/:id" element={<EntityDetail type="supplier"/>}/>
      <Route path="urunler" element={<Products/>}/>
      <Route path="urunler/:id" element={<ProductDetail/>}/>
      <Route path="parca-supersession" element={<PartSupersessions/>}/>
      <Route path="makineler" element={<Machines/>}/>
      <Route path="makineler/:id" element={<MachineDetail/>}/>
      <Route path="is-emirleri" element={<WorkOrders/>}/>
      <Route path="is-emirleri/:id" element={<WorkOrderDetail/>}/>
      <Route path="saha" element={<FieldWorkOrders/>}/>
      <Route path="saha/:id" element={<FieldWorkOrders/>}/>
      {/* Tarla Yönetimi V1 (mobil-erp#2). `/tarla` ile `/saha` AYRI şeylerdir:
          `/saha` bayinin servis iş emirleri, `/tarla` çiftçinin kendi üretimi. */}
      <Route path="tarla" element={<FarmDashboard/>}/>
      <Route path="tarla/ciftlikler" element={<Farms/>}/>
      <Route path="tarla/sezonlar" element={<CropSeasons/>}/>
      <Route path="tarla/faaliyetler" element={<FieldActivities/>}/>
      <Route path="tarla/gorevler" element={<FieldTasks/>}/>
      <Route path="tarla/hasat" element={<FieldHarvests/>}/>
      <Route path="tarla/hizli-giris" element={<QuickActivity/>}/>
      <Route path="tarla/parseller/:id" element={<ParcelDetail/>}/>
      {/* Hayvancılık AYRI bir alan (mobil-erp#17): tarlanın altına konsaydı
          "faaliyet" ile "aşı" aynı menüde görünür, ikisi farklı iş olurdu. */}
      <Route path="hayvancilik" element={<HerdDashboard/>}/>
      <Route path="hayvancilik/hayvanlar" element={<Animals/>}/>
      <Route path="hayvancilik/hayvanlar/:id" element={<AnimalDetail/>}/>
      <Route path="hayvancilik/saglik" element={<HerdHealth/>}/>
      <Route path="hayvancilik/doller" element={<HerdBreeding/>}/>
      <Route path="hayvancilik/verim" element={<HerdYields/>}/>
      {/* Gerçek Maliyet V1 (mobil-erp#24): oran bir PARA TANIMI, bu yüzden
          tarla/hayvancılık altında değil Yönetim tanımlarında. */}
      <Route path="tanimlar/maliyet-oranlari" element={<CostRates/>}/>
      <Route path="faturalar" element={<Invoices/>}/>
      <Route path="faturalar/:id" element={<InvoiceDetail/>}/>
      <Route path="odemeler" element={<Payments/>}/>
      <Route path="tahsis-defteri" element={<PaymentAllocations/>}/>
      <Route path="alacaklar" element={<Receivables/>}/>
      <Route path="nakit-yonetimi" element={<Finance/>}/>
      <Route path="tanimlar/harman-sezon" element={<HarvestSeasonAdmin/>}/>
      <Route path="stok-hareketleri" element={<StockMovements/>}/>
      <Route path="depo-transferleri/:id" element={<TransferDetail/>}/>
      <Route path="sube-transfer" element={<StockTransfer/>}/>
      <Route path="stok-sayimlari" element={<InventoryCounts/>}/>
      <Route path="stok-sayimlari/:id" element={<InventoryCountDetail/>}/>
      <Route path="depolar" element={<Warehouses/>}/>
      <Route path="depolar/:id" element={<WarehouseDetail/>}/>
      <Route path="raporlar" element={<Reports/>}/>
      <Route path="raporlar/alacak-yaslandirma" element={<ReceivablesAging/>}/>
      <Route path="raporlar/tedarikci-karsilastirma" element={<PurchaseComparison/>}/>
      <Route path="raporlar/satin-alma-panosu" element={<PurchaseDashboard/>}/>
      <Route path="raporlar/emilim-orani" element={<AbsorptionRate/>}/>
      <Route path="sezonsal-stok-plani" element={<SeasonalStockPlan/>}/>
      <Route path="analizler" element={<Insights/>}/>
      <Route path="firmalar" element={<Companies/>}/>
      <Route path="kullanicilar" element={<Users/>}/>
      <Route path="islem-gecmisi" element={<Audit/>}/>
      <Route path="aktivite" element={<ActivityLog/>}/>
      <Route path="yedekler" element={<Backups/>}/>
      <Route path="bildirimler" element={<Notifications/>}/>
      <Route path="bildirimler/sablonlar" element={<NotificationTemplates/>}/>
      <Route path="tedarikci-fiyatlari" element={<SupplierPrices/>}/>
    </Route>
    <Route path="*" element={<Navigate to="/" replace/>}/>
  </Routes></Suspense></BrowserRouter></ErrorBoundary>;
}
