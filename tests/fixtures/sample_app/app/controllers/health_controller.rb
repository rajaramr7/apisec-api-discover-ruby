class HealthController < ApplicationController
  skip_before_action :authenticate_user!

  def check; end
end
