from datetime import date,timedelta

SOURCES={
    'national_holidays':'https://www.asamblea.gob.sv/sites/default/files/documents/decretos/A740402C-BD65-41DF-894A-0B72A3106F35.pdf',
    'mothers_day':'https://www.asamblea.gob.sv/leyes-y-decretos/legislacion-genero/decretos-de-dias-conmemorativos',
    'fathers_day':'https://www.mtps.gob.sv/2019/06/14/mtps-17-de-junio-asueto-remunerado-para-las-personas-trabajadoras/',
    'august_scope':'https://www.mtps.gob.sv/2026/07/31/despliegue-nacional-de-inspeccion-laboral-durante-las-vacaciones-agostinas/',
    'easter_2026':'https://www.mtps.gob.sv/2026/03/23/voceria-oficial-informa-sobre-derechos-laborales-en-fechas-de-asueto/',
    'school_calendar':'https://www.mined.gob.sv/download/calendario-escolar/'}

def easter_sunday(year):
    a=year%19;b=year//100;c=year%100;d=b//4;e=b%4;f=(b+8)//25;g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30;i=c//4;k=c%4;l=(32+2*e+2*i-h-k)%7;m=(a+11*h+22*l)//451
    month=(h+l-7*m+114)//31;day=(h+l-7*m+114)%31+1
    return date(year,month,day)

def holiday_context(day,san_salvador_district=False):
    if not isinstance(day,date):raise TypeError('Expected a local date')
    fixed={(1,1):('año_nuevo','holiday_quiet'),(5,1):('día_trabajo','holiday_quiet'),
        (5,10):('día_madre','holiday_shopping'),(6,17):('día_padre','holiday_shopping'),
        (8,6):('divino_salvador','holiday_beach'),(9,15):('independencia','holiday_civic'),
        (11,2):('difuntos','holiday_civic'),(12,25):('navidad','holiday_quiet')}
    selected=fixed.get((day.month,day.day));national=selected is not None
    easter=easter_sunday(day.year)
    for delta,name,kind in ((-3,'jueves_santo','holiday_outbound'),(-2,'viernes_santo','holiday_beach'),(-1,'sábado_santo','holiday_beach')):
        if day==easter+timedelta(days=delta):selected=(name,kind);national=True
    local=bool(san_salvador_district and day.month==8 and day.day in (3,5))
    if local:selected=('fiestas_san_salvador','holiday_local')
    return {'name':selected[0] if selected else None,'holiday':selected[1] if selected else 'none',
        'national':national,'district_only':local,'school_break':None,
        'rules_checked_as_of':'2026-10-22','school_calendar_note':'Supply school-break context explicitly.'}
