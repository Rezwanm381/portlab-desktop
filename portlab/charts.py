"""Lightweight native plots for forecast evidence and simulated operating traces."""
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class ForecastPlot(QWidget):
    def __init__(self):
        super().__init__()
        self.record = None
        self.mode = 'forecast'
        self.setMinimumHeight(220)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('#f6f9fc'))
        if not self.record:
            p.setPen(QColor('#64748b'))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Import annual history to view prediction checks')
            return
        rec = self.record
        history = [(int(row['year']), float(row['value'])) for row in rec.get('history', [])]
        if not history:
            return
        future = ((int(rec['forecast_year']), float(rec['forecast']))
                  if rec.get('forecast') is not None else None)
        tests = [(int(row['year']), float(row['forecast'])) for row in rec.get('backtests', [])
                 if row.get('forecast') is not None]
        show_tests = self.mode == 'backtests'
        low, high = rec.get('uncertainty_low'), rec.get('uncertainty_high')
        values = [value for _, value in history] + ([future[1]] if future and not show_tests else [])
        values += [value for _, value in tests] if show_tests else []
        if future and not show_tests and high is not None:
            values.append(float(high))
        maximum = max(max(values) * 1.1, 1)
        x0 = history[0][0]
        x1 = history[-1][0] if show_tests or future is None else future[0]
        left, top, right, bottom = 86, 43, max(self.width()-48, 120), self.height()-43
        def xy(point):
            return QPointF(left+(point[0]-x0)/max(x1-x0,1)*(right-left),
                           bottom-point[1]/maximum*(bottom-top))
        p.setPen(QColor('#334155'))
        title = 'Held-out predictions' if show_tests else 'Annual history and following-year estimate'
        p.drawText(left, 23, f"{rec['port']} · {rec.get('unit',rec['metric'])} · {title}")
        for index in range(5):
            y = top+(bottom-top)*index/4
            p.setPen(QColor('#dbe4ed')); p.drawLine(left,int(y),right,int(y))
            p.setPen(QColor('#64748b')); p.drawText(4,int(y)+4,f'{maximum*(1-index/4):,.0f}')
        # This is a historical-error envelope, not a claimed confidence interval.
        if future and not show_tests and low is not None and high is not None:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(234,151,27,35))
            p.drawPolygon(QPolygonF([xy(history[-1]),xy((future[0],float(low))),xy((future[0],float(high)))]))
            p.setPen(QPen(QColor('#c0790d'),2))
            a,b = xy((future[0],float(low))),xy((future[0],float(high)))
            p.drawLine(a,b); p.drawLine(QPointF(a.x()-5,a.y()),QPointF(a.x()+5,a.y()))
            p.drawLine(QPointF(b.x()-5,b.y()),QPointF(b.x()+5,b.y()))
        p.setPen(QPen(QColor('#167d9a'),3)); p.setBrush(QColor('#167d9a'))
        for first,second in zip(history,history[1:]): p.drawLine(xy(first),xy(second))
        for point in history: p.drawEllipse(xy(point),3.5,3.5)
        if show_tests:
            p.setPen(QPen(QColor('#8154b8'),2,Qt.PenStyle.DashLine)); p.setBrush(QColor('#8154b8'))
            for first,second in zip(tests,tests[1:]): p.drawLine(xy(first),xy(second))
            for point in tests: p.drawEllipse(xy(point),4,4)
        elif future:
            p.setPen(QPen(QColor('#c0790d'),3,Qt.PenStyle.DashLine)); p.setBrush(QColor('#c0790d'))
            p.drawLine(xy(history[-1]),xy(future)); p.drawEllipse(xy(future),5,5)
        p.setPen(QColor('#64748b'))
        stride = 1 if x1-x0 <= 10 else max(1,(x1-x0)//8)
        for year in range(x0,x1+1,stride): p.drawText(int(xy((year,0)).x())-15,bottom+18,str(year))
        legend = ('Teal: actual annual values   Purple: predictions made before each held-out year'
                  if show_tests else 'Teal: supplied history   Amber: estimate and historical error envelope (not a confidence interval)')
        p.drawText(left,bottom+36,legend)


class OperationsPlot(QWidget):
    def __init__(self):
        super().__init__()
        self.result = None
        self.hour = 0
        self.setMinimumHeight(220)

    def paintEvent(self,event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('#f6f9fc'))
        samples = self.result.kpis.get('stock_flow_samples',[]) if self.result else []
        if not samples:
            p.setPen(QColor('#64748b')); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'Run a scenario to view queues and yard use')
            return
        cutoff = float(self.result.kpis.get('simulated_hours',self.result.config.horizon_hours))
        queue_max = max(max(row.get('queued_calls',0) for row in samples),1)
        left,top,right,bottom = 65,43,self.width()-68,self.height()-44
        x = lambda time:left+time/max(cutoff,.001)*(right-left)
        yq = lambda value:bottom-value/queue_max*(bottom-top)
        yf = lambda value:bottom-value*(bottom-top)
        p.setPen(QColor('#334155')); p.drawText(left,23,'Recorded queue and yard use · run horizon')
        for episode in self.result.checks.get('pressure_episodes',[]):
            start = episode.get('eligible_hour')
            if start is None: continue
            end = episode.get('end_hour')
            end = cutoff if end is None else min(float(end),cutoff)
            if start >= end: continue
            p.fillRect(int(x(start)),top,max(1,int(x(end)-x(start))),bottom-top,QColor(25,137,109,25))
        for index in range(5):
            y = top+(bottom-top)*index/4
            p.setPen(QColor('#dbe4ed'));p.drawLine(left,int(y),right,int(y))
            p.setPen(QColor('#64748b'));p.drawText(6,int(y)+4,f'{queue_max*(1-index/4):.0f}')
            p.drawText(right+8,int(y)+4,f'{100*(1-index/4):.0f}%')
        for field,transform,color in [('queued_calls',yq,'#167d9a'),('yard_fill_fraction',yf,'#c0790d')]:
            p.setPen(QPen(QColor(color),2))
            for first,second in zip(samples,samples[1:]):
                p.drawLine(QPointF(x(first['time']),transform(first[field])),QPointF(x(second['time']),transform(second[field])))
        p.setPen(QPen(QColor('#475569'),1,Qt.PenStyle.DashLine));p.drawLine(int(x(self.hour)),top,int(x(self.hour)),bottom)
        p.setPen(QColor('#64748b'))
        for index in range(5):p.drawText(int(x(cutoff*index/4))-12,bottom+18,f'{cutoff*index/4:.0f}h')
        p.drawText(left,bottom+36,'Teal: waiting calls (left)   Amber: yard fill (right)   Shaded: eligible pressure episodes')
