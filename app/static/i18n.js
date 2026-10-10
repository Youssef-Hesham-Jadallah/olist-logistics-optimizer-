// English / Arabic (RTL). Missing Arabic keys fall back to English.
const I18N = {
  en: {
    overview: 'Control room', shipments: 'Shipments', compare: 'Compare carriers', sourcing: 'Nearest source', batch: 'Batch optimizer', whatif: 'What-if lab', carriers: 'Carriers', analytics: 'Analytics', backtest: 'Backtest', notifications: 'Notifications', import: 'Data import',
    signin: 'Sign in', username: 'Username', password: 'Password', logout: 'Sign out', search_ph: 'Search shipments, pages…', cmd_ph: 'Type to jump to a page or find a shipment…',
    total: 'Total shipments', pending: 'Pending', booked: 'Booked', in_transit: 'In transit', delivered: 'Delivered', canceled: 'Canceled', avg_hist: 'Avg freight (history)', avg_transit: 'Avg transit (days)', late_rate: 'Historical late rate', avg_booked: 'Avg booked price',
    simulated_notice: 'Simulated carriers calibrated on Olist 2016-2018 data — not live carrier prices.',
    hero_title: 'Ship smarter across Brazil', hero_sub: 'Every order, every carrier, every route: price, delivery time and late risk — optimised in one place.', flows_legend: 'Top shipment corridors (live arcs)',
    origin: 'Origin', destination: 'Destination', weight: 'Weight (kg)', value: 'Declared value (R$)', dims: 'L×H×W cm', deadline_days: 'Deadline (days)', objective: 'Objective', compare_btn: 'Compare carriers',
    carrier: 'Carrier', price: 'Price', eta: 'Delivery time', eta_p50: 'ETA P50', eta_p90: 'ETA P90', risk: 'Late risk', expected_cost: 'Expected cost', why: 'Why', choose: 'Choose', status: 'Status', recommended: 'Recommended', none_feasible: 'No carrier can meet this deadline', excluded: 'Excluded carriers',
    prev: 'Prev', next: 'Next', create: 'Create', new_shipment: 'New shipment', notify_email: 'Notify e-mail', run: 'Run', apply_plan: 'Apply plan (book them)', relax: 'Relax impossible deadlines', orders: 'Orders', product: 'Product', qty: 'Quantity', seller: 'Seller', distance: 'Distance (km)',
    health: 'Health', coverage: 'Coverage', capacity: 'Capacity today', outage: 'Simulate outage', recover: 'Recover', reroute: 'Re-route', tracking: 'Tracking', assign: 'Assign', share_link: 'Customer tracking link', print_label: 'Print label', copied: 'Link copied',
    late_penalty: 'Cost of a late delivery (R$)', season_note: 'Peak-season delay factors applied (Nov/Dec)', o_cost: 'Lowest cost', o_speed: 'Fastest', o_reliability: 'Most reliable', o_balanced: 'Balanced', o_expected_cost: 'Lowest expected cost', o_custom: 'Custom weights',
    race_title: 'Delivery-time race (P50 → P90)', route: 'Route', loading: 'Loading…', empty: 'Nothing here yet', scorecard: 'Network scorecard', timeline: 'Shipments over time', routes_cost: 'Average cost & transit per route', sla: 'SLA breach rate', status_mix: 'Status mix',
    policies: 'Policies on the same orders', frontier: 'Cost vs lateness (bottom-left is better)', model_quality: 'Model quality (hold-out year)', ship_to: 'Track your parcel', enter_code: 'Enter tracking code', track_btn: 'Track', promised: 'Promised by', expected_on: 'Expected around', latest_likely: 'Latest likely', not_found: 'Tracking code not found'
  },
  ar: {
    overview: 'غرفة التحكم', shipments: 'الشحنات', compare: 'مقارنة شركات الشحن', sourcing: 'أقرب مصدر', batch: 'التخصيص الجماعي', whatif: 'معمل ماذا لو', carriers: 'شركات الشحن', analytics: 'التحليلات', backtest: 'الاختبار التاريخي', notifications: 'الإشعارات', import: 'استيراد البيانات',
    signin: 'تسجيل الدخول', username: 'اسم المستخدم', password: 'كلمة المرور', logout: 'خروج', search_ph: 'ابحث عن شحنة أو صفحة…', cmd_ph: 'اكتب للانتقال لصفحة أو للبحث عن شحنة…',
    total: 'إجمالي الشحنات', pending: 'معلّقة', booked: 'محجوزة', in_transit: 'في الطريق', delivered: 'تم التسليم', canceled: 'ملغية', avg_hist: 'متوسط الشحن (تاريخي)', avg_transit: 'متوسط مدة النقل (أيام)', late_rate: 'نسبة التأخير التاريخية', avg_booked: 'متوسط السعر المحجوز',
    simulated_notice: 'شركات شحن محاكاة معايَرة على بيانات Olist ‏2016-2018 — وليست أسعاراً حية.',
    hero_title: 'اشحن بذكاء في أنحاء البرازيل', hero_sub: 'كل أوردر وكل شركة وكل مسار: السعر ووقت التوصيل واحتمال التأخير — محسّنة في مكان واحد.', flows_legend: 'أكثر مسارات الشحن ازدحاماً (حيّة)',
    origin: 'من', destination: 'إلى', weight: 'الوزن (كجم)', value: 'القيمة المُعلنة (R$)', dims: 'الأبعاد سم', deadline_days: 'الموعد النهائي (أيام)', objective: 'الهدف', compare_btn: 'قارن الشركات',
    carrier: 'الشركة', price: 'السعر', eta: 'وقت التوصيل', eta_p50: 'المتوقع P50', eta_p90: 'الأسوأ المرجّح P90', risk: 'احتمال التأخير', expected_cost: 'التكلفة المتوقعة', why: 'السبب', choose: 'اختيار', status: 'الحالة', recommended: 'المرشّحة', none_feasible: 'لا توجد شركة تحقق هذا الموعد', excluded: 'شركات مستبعدة',
    prev: 'السابق', next: 'التالي', create: 'إنشاء', new_shipment: 'شحنة جديدة', notify_email: 'إيميل الإشعارات', run: 'تشغيل', apply_plan: 'تطبيق الخطة (حجز)', relax: 'تجاهل المواعيد المستحيلة', orders: 'الطلبات', product: 'المنتج', qty: 'الكمية', seller: 'البائع', distance: 'المسافة (كم)',
    health: 'الحالة', coverage: 'التغطية', capacity: 'السعة اليوم', outage: 'محاكاة عطل', recover: 'استعادة', reroute: 'إعادة توجيه', tracking: 'التتبع', assign: 'تعيين', share_link: 'رابط تتبع العميل', print_label: 'طباعة البوليصة', copied: 'تم نسخ الرابط',
    late_penalty: 'تكلفة التأخير الواحد (R$)', season_note: 'تم تطبيق عامل الموسم (نوفمبر/ديسمبر)', o_cost: 'الأرخص', o_speed: 'الأسرع', o_reliability: 'الأكثر موثوقية', o_balanced: 'متوازن', o_expected_cost: 'أقل تكلفة متوقعة', o_custom: 'أوزان مخصصة',
    race_title: 'سباق وقت التوصيل (P50 ← P90)', route: 'المسار', loading: 'جارٍ التحميل…', empty: 'لا يوجد شيء بعد', scorecard: 'بطاقة أداء الشبكة', timeline: 'الشحنات عبر الزمن', routes_cost: 'متوسط التكلفة والمدة لكل مسار', sla: 'نسبة خرق الموعد', status_mix: 'توزيع الحالات',
    policies: 'السياسات على نفس الأوردرات', frontier: 'التكلفة مقابل التأخير (الأفضل أسفل اليسار)', model_quality: 'جودة النموذج (سنة الاختبار)', ship_to: 'تتبّع شحنتك', enter_code: 'أدخل كود التتبع', track_btn: 'تتبّع', promised: 'الموعد الموعود', expected_on: 'متوقع حوالي', latest_likely: 'أبعد موعد مرجّح', not_found: 'كود التتبع غير موجود'
  }
};
