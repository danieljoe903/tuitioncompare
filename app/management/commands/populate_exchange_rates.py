from django.core.management.base import BaseCommand
from app.models import ExchangeRate

class Command(BaseCommand):
    help = "Populate initial exchange rates"

    def handle(self, *args, **kwargs):
        exchange_rates_data = [
            {'code': 'GBP', 'name': 'British Pound', 'symbol': '£', 'rate': 1.0000},
            {'code': 'EUR', 'name': 'Euro', 'symbol': '€', 'rate': 1.1700},
            {'code': 'USD', 'name': 'US Dollar', 'symbol': '$', 'rate': 1.2700},
            {'code': 'NGN', 'name': 'Nigerian Naira', 'symbol': '₦', 'rate': 1850.0000},
            {'code': 'GHS', 'name': 'Ghanaian Cedi', 'symbol': '₵', 'rate': 15.5000},
            {'code': 'KES', 'name': 'Kenyan Shilling', 'symbol': 'KSh', 'rate': 190.0000},
            {'code': 'ZAR', 'name': 'South African Rand', 'symbol': 'R', 'rate': 23.0000},
            {'code': 'INR', 'name': 'Indian Rupee', 'symbol': '₹', 'rate': 106.0000},
            {'code': 'CNY', 'name': 'Chinese Yuan', 'symbol': '¥', 'rate': 9.2000},
            {'code': 'PKR', 'name': 'Pakistani Rupee', 'symbol': 'Rs', 'rate': 353.0000},
            {'code': 'BDT', 'name': 'Bangladeshi Taka', 'symbol': '৳', 'rate': 140.0000},
        ]
        
        created_count = 0
        updated_count = 0
        
        for data in exchange_rates_data:
            rate, created = ExchangeRate.objects.update_or_create(
                currency_code=data['code'],
                defaults={
                    'currency_name': data['name'],
                    'currency_symbol': data['symbol'],
                    'rate_to_gbp': data['rate'],
                    'is_active': True
                }
            )
            
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'Created: {data["code"]} - {data["name"]} (1 GBP = {data["rate"]} {data["code"]})'))
            else:
                updated_count += 1
                self.stdout.write(self.style.WARNING(f'Updated: {data["code"]} - {data["name"]} (1 GBP = {data["rate"]} {data["code"]})'))
        
        self.stdout.write(self.style.SUCCESS(f'\nTotal: {created_count} created, {updated_count} updated'))
